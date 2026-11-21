"""AI testers never inherit administration or another user's memory scope."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId

from app.api.ai_preview import get_preview_extractor
from app.api.conversations import get_conversation_service
from app.api.memories import get_memory_reader
from app.api.memory import get_memory_jobs
from app.api.topic_summary import get_summary_service
from app.core.config import Settings, get_settings
from app.main import app
from app.services.memory_worker import MemoryWorker, process_job
from test_records import context as record_context, login_as, write_headers


def enable_test_user():
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(
        settings, admin_uid='user-a', ai_allowed_uids=frozenset({'user-b'}), ai_capture_enabled=True)
    app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda q: None)}


def test_capabilities_and_enqueuing_do_not_grant_admin(record_context):
    client, collection = record_context
    enable_test_user()
    login_as(client, 'b')
    assert client.get('/auth/capabilities').json() == {
        'is_admin': False, 'ai_enabled': True, 'capture_enabled': True}
    assert client.get('/admin/me').status_code == 403
    assert client.get('/admin/users').status_code == 403
    assert client.post('/records', json={'content': 'test account'}, headers=write_headers(client)).status_code == 201
    assert collection.rows[0]['user_uid'] == 'user-b'
    assert collection.rows[0]['ai_processing']['state'] == 'pending'
    login_as(client)
    assert client.get('/auth/capabilities').json()['is_admin'] is True


def test_grant_is_not_a_login_allowlist_and_disabled_accounts_stay_blocked(record_context):
    client, _ = record_context
    enable_test_user()
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, allowed_uids=frozenset())
    login_as(client, 'b')
    assert client.get('/auth/capabilities').status_code == 403
    app.state.database_connection.database['access_users'] = SimpleNamespace(find_one=lambda q: {'enabled': False})
    assert client.get('/auth/capabilities').status_code == 403


def test_test_user_memory_and_summary_are_scoped_to_verified_uid(record_context):
    client, _ = record_context
    enable_test_user()
    reader, service, jobs = Mock(), Mock(), Mock()
    reader.memories.recent.return_value = []
    reader.get_eligible.return_value = None
    service.read.return_value = {'state': 'missing'}
    service.generate.return_value = {'state': 'succeeded'}
    jobs.get.return_value = None
    app.dependency_overrides[get_memory_reader] = lambda: reader
    app.dependency_overrides[get_summary_service] = lambda: service
    app.dependency_overrides[get_memory_jobs] = lambda: jobs
    login_as(client, 'b')
    assert client.get('/memories/topics').status_code == 200
    reader.memories.recent.assert_called_once_with('user-b')
    assert client.get('/memories/' + 'a'*64).status_code == 404
    reader.get_eligible.assert_called_once_with('user-b', 'a'*64)
    assert client.get('/topic-summary', params={'topic': '旅行'}).status_code == 200
    service.read.assert_called_once_with('user-b', '旅行')
    assert client.post('/topic-summary', json={'topic': '旅行'}, headers=write_headers(client)).status_code == 200
    service.generate.assert_called_once_with('user-b', '旅行')
    record_id = str(ObjectId())
    assert client.get(f'/records/{record_id}/memory').status_code == 404
    jobs.get.assert_called_once_with('user-b', record_id)
    assert client.get('/memories/topics', headers={'X-Raphael-Account': 'user-a'}).status_code == 409


def test_test_user_conversation_history_uses_own_uid(record_context):
    client, _ = record_context
    enable_test_user()
    service = Mock()
    service.repository.page.return_value = []
    service.public.return_value = []
    app.dependency_overrides[get_conversation_service] = lambda: service
    login_as(client, 'b')
    assert client.get('/conversations').status_code == 200
    service.repository.page.assert_called_once_with('user-b', 10, None)
    assert client.get('/conversations', headers={'X-Raphael-Account': 'user-a'}).status_code == 409


def test_unlisted_account_cannot_use_ai(record_context):
    client, _ = record_context
    enable_test_user()
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, ai_allowed_uids=frozenset())
    app.dependency_overrides[get_memory_reader] = lambda: Mock()
    app.dependency_overrides[get_conversation_service] = lambda: Mock()
    app.dependency_overrides[get_summary_service] = lambda: Mock()
    model = Mock()
    app.dependency_overrides[get_preview_extractor] = lambda: model
    login_as(client, 'b')
    assert client.get('/auth/capabilities').json()['ai_enabled'] is False
    for path in ['/memories/topics', '/conversations', '/topic-summary?topic=test']:
        assert client.get(path).status_code == 403
    assert client.post('/admin/ai-preview', json={'content': 'test'}, headers=write_headers(client)).status_code == 403
    model.analyze.assert_not_called()


def test_worker_checks_dynamic_disabled_override_and_legacy_fallback():
    access = Mock()
    settings = Settings(admin_uid='owner', ai_allowed_uids=frozenset({'tester'}), allowed_uids=frozenset({'tester'}))
    worker = MemoryWorker({'access_users': access}, settings)
    access.find_one.return_value = None
    assert worker.account_enabled('tester')
    access.find_one.return_value = {'enabled': False}
    assert not worker.account_enabled('tester')
    access.find_one.return_value = {'enabled': True}
    assert worker.account_enabled('tester')
    assert worker.account_enabled('owner')
    assert not worker.account_enabled('unlisted')


def test_revocation_before_capture_or_after_extraction_stops_new_steps():
    jobs, capture, router = Mock(), Mock(), Mock()
    row = {'_id': ObjectId(), 'user_uid': 'tester'}
    process_job(jobs, capture, router, row, authorized=lambda: False)
    capture.capture.assert_not_called()
    jobs.finish.assert_called_once_with(row, 'failed', error='access_revoked')
    jobs.reset_mock()
    capture.capture.return_value = ({'_id': 'analysis', 'memories': [{'_id': 'item'}]}, False)
    checks = iter([True, False])
    process_job(jobs, capture, router, row, authorized=lambda: next(checks))
    router.route.assert_not_called()
    jobs.finish.assert_called_once_with(row, 'failed', error='access_revoked')


def test_worker_claims_each_allowed_user_once_and_skips_disabled(monkeypatch):
    from app.services import memory_worker
    jobs = Mock()
    jobs.claim.side_effect = lambda uid: {'_id': ObjectId(), 'user_uid': uid}
    monkeypatch.setattr(memory_worker, 'MemoryJobsRepository', lambda records: jobs)
    monkeypatch.setattr(memory_worker, 'build_pipeline', lambda db, settings: (Mock(), Mock()))
    settings = Settings(admin_uid='owner', ai_allowed_uids=frozenset({'tester', 'disabled'}), ai_capture_enabled=True)
    access = Mock()
    access.find_one.side_effect = lambda q: {'enabled': q['_id'] != 'disabled'}
    worker = MemoryWorker({'records': Mock(), 'access_users': access}, settings)
    processed = []
    def process(jobs, capture, router, row, stopped, authorized):
        assert authorized()
        processed.append(row['user_uid'])
        if len(processed) == 2: worker.stop_event.set()
    monkeypatch.setattr(memory_worker, 'process_job', process)
    worker.run()
    assert processed == ['owner', 'tester']
    assert [call.args[0] for call in jobs.claim.call_args_list] == processed
