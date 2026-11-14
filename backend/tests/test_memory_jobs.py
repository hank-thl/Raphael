from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
import pytest

from app.api.memory import get_memory_jobs
from app.core.config import get_settings
from app.main import app
from app.repositories.memory_jobs import MemoryJobsRepository, now_utc
from app.services.gemini import GeminiError
from app.services.memory_capture import CaptureError
from app.services.memory_worker import process_job
from test_records import context as record_context, login_as, write_headers


def enable_owner():
    app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda query: None)}
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, admin_uid='user-a', ai_capture_enabled=True)


def test_only_enabled_owner_new_records_are_enqueued_atomically(record_context):
    client, collection = record_context
    enable_owner()
    login_as(client)
    response = client.post('/records', json={'content': 'new personal message'}, headers=write_headers(client))
    assert response.status_code == 201
    assert set(response.json()) == {'id', 'content', 'created_at'}
    assert collection.rows[0]['ai_processing']['state'] == 'pending'
    assert collection.rows[0]['ai_processing']['attempts'] == 0
    login_as(client, 'b')
    assert client.post('/records', json={'content': 'other user'}, headers=write_headers(client)).status_code == 201
    assert 'ai_processing' not in collection.rows[1]


def test_feature_default_off_keeps_original_capture_behavior(record_context):
    client, collection = record_context
    login_as(client)
    client.post('/records', json={'content': 'saved only'}, headers=write_headers(client))
    assert 'ai_processing' not in collection.rows[0]


def test_status_scoped_to_owner_without_exposing_analysis(record_context):
    client, collection = record_context
    enable_owner()
    jobs = Mock()
    jobs.get.return_value = {'ai_processing': {'state': 'failed', 'attempts': 1, 'error': 'read_timeout',
                                              'analysis_id': 'private', 'routing': ['private']}}
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    login_as(client)
    identifier = str(ObjectId())
    response = client.get(f'/records/{identifier}/memory', headers={'X-Raphael-Account': 'user-b'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {'state': 'failed', 'attempts': 1, 'error': 'read_timeout'}
    jobs.get.assert_called_once_with('user-a', identifier)
    login_as(client, 'b')
    assert client.get(f'/records/{identifier}/memory').status_code == 403
    assert jobs.get.call_count == 1


@pytest.mark.parametrize('method,suffix', [('GET', 'memory'), ('POST', 'memory/retry')])
def test_job_endpoints_require_login_and_feature_flag(record_context, method, suffix):
    client, collection = record_context
    jobs = Mock()
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    path = f'/records/{ObjectId()}/{suffix}'
    assert client.request(method, path).status_code in {401, 403}
    login_as(client)
    assert client.request(method, path, headers=write_headers(client)).status_code == 403
    jobs.get.assert_not_called()
    jobs.retry.assert_not_called()


def test_retry_requires_csrf_failed_job_and_never_backfills_old_records(record_context):
    client, collection = record_context
    enable_owner()
    jobs = Mock()
    jobs.get.return_value = {}  # Existing owner record, no ai_processing.
    jobs.retry.return_value = None
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    login_as(client)
    identifier = str(ObjectId())
    path = f'/records/{identifier}/memory/retry'
    assert client.post(path).status_code == 403
    jobs.retry.assert_not_called()
    assert client.post(path, headers=write_headers(client)).status_code == 409
    jobs.retry.assert_called_once_with('user-a', identifier)
    jobs.retry.return_value = {'ai_processing': {'state': 'pending', 'attempts': 1, 'error': None}}
    assert client.post(path, headers=write_headers(client)).status_code == 202


@pytest.mark.parametrize('method,suffix', [('GET', 'memory'), ('POST', 'memory/retry')])
def test_missing_deleted_and_other_owner_records_are_not_exposed(record_context, method, suffix):
    client, collection = record_context
    enable_owner()
    jobs = Mock()
    jobs.get.return_value = None
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    login_as(client)
    response = client.request(method, f'/records/{ObjectId()}/{suffix}', headers=write_headers(client))
    assert response.status_code == 404
    jobs.retry.assert_not_called()


def test_claim_is_atomic_ordered_scoped_and_finish_uses_attempt_token():
    collection = Mock()
    jobs = MemoryJobsRepository(collection)
    jobs.claim('owner')
    query, update = collection.find_one_and_update.call_args.args
    assert query == {'user_uid': 'owner', 'deleted_at': None, 'ai_processing.state': 'pending'}
    assert update['$inc'] == {'ai_processing.attempts': 1}
    assert collection.find_one_and_update.call_args.kwargs['sort'] == [('created_at', 1), ('_id', 1)]
    row = {'_id': ObjectId(), 'user_uid': 'owner', 'ai_processing': {'token': 'attempt-token'}}
    collection.update_one.return_value = SimpleNamespace(matched_count=0)
    assert jobs.finish(row, 'succeeded') is False
    assert collection.update_one.call_args.args[0] == {
        '_id': row['_id'], 'user_uid': 'owner', 'deleted_at': None,
        'ai_processing.state': 'running', 'ai_processing.token': 'attempt-token'}
    jobs.retry('owner', str(row['_id']))
    assert collection.find_one_and_update.call_args.args[0]['ai_processing.state'] == 'failed'


def fixture_job():
    jobs, capture, router = Mock(), Mock(), Mock()
    jobs.touch.return_value = True
    row = {'_id': ObjectId(), 'user_uid': 'owner', 'ai_processing': {'token': 'token'}}
    capture.capture.return_value = ({'_id': 'analysis', 'memories': [{'_id': 'candidate'}]}, False)
    router.route.return_value = {'action': 'new', 'memory_id': 'memory', 'revision': 1}
    return jobs, capture, router, row


def test_worker_success_and_skip_are_distinct_from_failure():
    jobs, capture, router, row = fixture_job()
    process_job(jobs, capture, router, row)
    capture.capture.assert_called_once_with('owner', str(row['_id']))
    router.route.assert_called_once_with('owner', 'analysis', 'candidate')
    assert jobs.finish.call_args.args[1] == 'succeeded'
    capture.capture.return_value = ({'_id': 'skip-analysis', 'memories': []}, False)
    router.reset_mock()
    process_job(jobs, capture, router, row)
    router.route.assert_not_called()
    assert jobs.finish.call_args.kwargs['routing'] == []


@pytest.mark.parametrize('error,expected', [(GeminiError('read_timeout'), 'read_timeout'),
    (RuntimeError('private-password-and-record'), 'analysis_failed'),
    (CaptureError('source_unavailable'), 'source_unavailable')])
def test_worker_failure_preserves_raw_record_and_never_stores_raw_error(error, expected):
    jobs, capture, router, row = fixture_job()
    capture.capture.side_effect = error
    process_job(jobs, capture, router, row)
    jobs.finish.assert_called_once_with(row, 'failed', error=expected)
    router.route.assert_not_called()
    assert 'private' not in str(jobs.finish.call_args)


def test_worker_stop_or_deleted_record_prevents_model_call():
    jobs, capture, router, row = fixture_job()
    process_job(jobs, capture, router, row, stopped=lambda: True)
    capture.capture.assert_not_called()
    jobs.touch.return_value = False
    process_job(jobs, capture, router, row)
    capture.capture.assert_not_called()


def test_expired_running_jobs_are_failed_not_automatically_requeued():
    collection = Mock()
    jobs = MemoryJobsRepository(collection)
    jobs.expire('owner')
    query, update = collection.update_many.call_args.args
    assert query['user_uid'] == 'owner' and query['ai_processing.state'] == 'running'
    assert '$lte' in query['ai_processing.lease_until']
    assert update['$set']['ai_processing.state'] == 'failed'
    assert update['$set']['ai_processing.error'] == 'interrupted'


def test_worker_claims_only_configured_owner_and_stops_between_jobs(monkeypatch):
    from threading import Event
    from app.core.config import Settings
    from app.services import memory_worker
    jobs = Mock()
    jobs.claim.return_value = {'_id': ObjectId(), 'user_uid': 'owner'}
    monkeypatch.setattr(memory_worker, 'MemoryJobsRepository', lambda records: jobs)
    monkeypatch.setattr(memory_worker, 'build_pipeline', lambda db, settings: (Mock(), Mock()))
    worker = memory_worker.MemoryWorker({'records': Mock()}, Settings(admin_uid='owner', ai_capture_enabled=True))
    def process(jobs_arg, capture, router, row, stopped):
        assert jobs_arg is jobs and row['user_uid'] == 'owner'
        worker.stop_event.set()
    monkeypatch.setattr(memory_worker, 'process_job', process)
    worker.run()
    jobs.expire.assert_called_once_with('owner')
    jobs.claim.assert_called_once_with('owner')


def test_worker_database_failure_log_is_fixed_and_preserves_queue(monkeypatch, caplog):
    from app.core.config import Settings
    from app.services import memory_worker
    jobs = Mock()
    jobs.expire.side_effect = RuntimeError('mongodb://private-password@host')
    monkeypatch.setattr(memory_worker, 'MemoryJobsRepository', lambda records: jobs)
    monkeypatch.setattr(memory_worker, 'build_pipeline', lambda db, settings: (Mock(), Mock()))
    worker = memory_worker.MemoryWorker({'records': Mock()}, Settings(admin_uid='owner', ai_capture_enabled=True))
    monkeypatch.setattr(worker.stop_event, 'wait', lambda seconds: worker.stop_event.set())
    worker.run()
    jobs.claim.assert_not_called()
    assert 'pending jobs remain stored' in caplog.text and 'private-password' not in caplog.text


def test_retry_endpoint_database_failure_never_leaks_credentials(record_context):
    from pymongo.errors import OperationFailure
    client, collection = record_context
    enable_owner()
    jobs = Mock()
    jobs.expire.side_effect = OperationFailure('mongodb://private-password@host')
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    login_as(client)
    response = client.post(f'/records/{ObjectId()}/memory/retry', headers=write_headers(client))
    assert response.status_code == 503 and 'private-password' not in response.text


def test_client_cannot_request_ai_for_other_or_old_record(record_context):
    client, collection = record_context
    enable_owner()
    login_as(client)
    response = client.post('/records', json={'content': 'test', 'ai_processing': {'state': 'pending'}},
                           headers=write_headers(client))
    assert response.status_code == 422 and not collection.rows
