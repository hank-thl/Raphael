from copy import deepcopy
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings, get_settings
from app.repositories.embedding_jobs import EmbeddingJobsRepository, pending_embedding
from app.repositories.memories import MemoriesRepository
from app.services.embedding_worker import process_embedding_job
from app.services.embeddings import EmbeddingError, PROFILE
from app.services.memory_embeddings import MemoryEmbeddingError
from test_memory_embeddings import ctx
from test_memory_answer import setup as answer_setup


def test_new_memory_and_revision_enqueue_atomically_without_modifying_history():
    collection = Mock()
    repository = MemoriesRepository(collection)
    row = {'_id': 'id', 'user_uid': 'owner', 'revision': 1, 'versions': []}
    saved = repository.create_once(row)
    assert saved['embedding_processing']['state'] == 'pending'
    assert saved['embedding_processing']['revision'] == 1
    assert 'embedding_processing' not in row
    assert collection.insert_one.call_args.args[0] == saved
    repository.append('owner', 'id', 1, {'created_at': 'now'})
    query, update = collection.find_one_and_update.call_args.args
    assert query['revision'] == 1 and query['user_uid'] == 'owner'
    assert update['$set']['embedding_processing']['revision'] == 2
    assert update['$set']['embedding_processing']['state'] == 'pending'
    assert update['$push']['versions'] == {'created_at': 'now'}


def test_claim_and_finish_bind_uid_revision_profile_and_attempt_token():
    collection = Mock()
    collection.update_one.return_value = SimpleNamespace(matched_count=0)
    jobs = EmbeddingJobsRepository(collection)
    jobs.claim('owner')
    query, update = collection.find_one_and_update.call_args.args
    assert query['user_uid'] == 'owner' and query['embedding_processing.state'] == 'pending'
    assert query['embedding_processing.profile'] == PROFILE
    assert update['$inc'] == {'embedding_processing.attempts': 1}
    row = {'_id': 'id', 'user_uid': 'owner', 'revision': 2, 'embedding_processing': {'token': 'token'}}
    assert jobs.finish(row, 'succeeded') is False
    finish_query = collection.update_one.call_args.args[0]
    assert finish_query['revision'] == 2 and finish_query['embedding_processing.revision'] == 2
    assert finish_query['user_uid'] == 'owner' and finish_query['embedding_processing.token'] == 'token'


def test_targeted_claim_preserves_owner_and_pending_guards():
    collection = Mock()
    EmbeddingJobsRepository(collection).claim('owner', memory_id='target')
    query = collection.find_one_and_update.call_args.args[0]
    assert query['_id'] == 'target' and query['user_uid'] == 'owner'
    assert query['embedding_processing.state'] == 'pending'
    assert query['embedding_processing.profile'] == PROFILE


def test_expiration_does_not_automatically_retry_or_enqueue_old_memory():
    collection = Mock(); jobs = EmbeddingJobsRepository(collection)
    jobs.expire('owner')
    query, update = collection.update_many.call_args.args
    assert query['user_uid'] == 'owner' and query['embedding_processing.state'] == 'running'
    assert update['$set']['embedding_processing.state'] == 'failed'
    jobs.retry('owner', 'id', 2)
    query = collection.find_one_and_update.call_args.args[0]
    assert query['embedding_processing.state'] == 'failed' and query['embedding_processing.revision'] == 2


def test_worker_saves_then_reuses_vector_without_reanalysis(ctx):
    service, reader, repo, provider, memory = ctx
    jobs = Mock(); jobs.touch.return_value = True
    row = {**memory, 'embedding_processing': {'token': 'token'}}
    process_embedding_job(jobs, service, row)
    assert jobs.finish.call_args.args == (row, 'succeeded')
    assert jobs.finish.call_args.kwargs['reused'] is False
    process_embedding_job(jobs, service, row)
    assert jobs.finish.call_args.kwargs['reused'] is True
    assert provider.document.call_count == 1
    assert memory['versions'][0]['content']['text'] == '今天上陶藝課很開心'


@pytest.mark.parametrize('reason', ['rate_limited', 'read_timeout', 'network_error'])
def test_provider_failure_is_independent_and_not_retried(ctx, reason):
    service, reader, repo, provider, memory = ctx
    jobs = Mock(); jobs.touch.return_value = True
    provider.document.side_effect = EmbeddingError(reason)
    process_embedding_job(jobs, service, {**memory, 'embedding_processing': {'token': 'token'}})
    assert jobs.finish.call_args.args[1] == 'failed'
    assert jobs.finish.call_args.kwargs['error'] == reason
    assert provider.document.call_count == 1
    repo.create_once.assert_not_called()


def test_private_error_body_is_not_persisted(ctx):
    service, reader, repo, provider, memory = ctx
    jobs = Mock(); jobs.touch.return_value = True
    provider.document.side_effect = RuntimeError('mongodb://password@private')
    process_embedding_job(jobs, service, {**memory, 'embedding_processing': {'token': 'token'}})
    assert jobs.finish.call_args.kwargs['error'] == 'embedding_failed'
    assert 'password' not in str(jobs.finish.call_args)


@pytest.mark.parametrize('guard', ['stop', 'revoked', 'token_lost', 'source_deleted', 'new_revision'])
def test_invalid_or_revoked_job_never_calls_provider(ctx, guard):
    service, reader, repo, provider, memory = ctx
    jobs = Mock(); jobs.touch.return_value = guard != 'token_lost'
    row = deepcopy(memory); row['embedding_processing'] = {'token': 'token'}
    if guard == 'source_deleted': reader.get_eligible_many.side_effect = lambda uid, rows: []
    if guard == 'new_revision': memory['revision'] += 1
    process_embedding_job(jobs, service, row, stopped=lambda: guard == 'stop', authorized=lambda: guard != 'revoked')
    provider.document.assert_not_called()
    assert jobs.finish.call_args.args[1] == 'failed'


def test_revoked_access_after_provider_prevents_cache_write(ctx):
    service, reader, repo, provider, memory = ctx
    jobs = Mock(); jobs.touch.return_value = True
    checks = iter([True, True, True, False])
    process_embedding_job(jobs, service, {**memory, 'embedding_processing': {'token': 'token'}},
                          authorized=lambda: next(checks))
    assert provider.document.call_count == 1
    repo.create_once.assert_not_called()
    assert jobs.finish.call_args.kwargs['error'] == 'access_revoked'


def test_embedding_only_worker_does_not_start_analysis_or_unlisted_accounts(monkeypatch):
    from app.services import memory_worker
    analysis_jobs, embedding_jobs, service = Mock(), Mock(), Mock()
    monkeypatch.setattr(memory_worker, 'MemoryJobsRepository', lambda records: analysis_jobs)
    monkeypatch.setattr(memory_worker, 'EmbeddingJobsRepository', lambda memories: embedding_jobs)
    monkeypatch.setattr(memory_worker, 'build_pipeline', lambda db, settings: (Mock(), Mock()))
    monkeypatch.setattr(memory_worker, 'MemoryEmbeddingService', lambda *args: service)
    settings = Settings(admin_uid='owner', ai_allowed_uids=frozenset({'unlisted'}),
                        ai_capture_enabled=False, memory_embedding_enabled=True)
    access = Mock(); access.find_one.return_value = {'enabled': True}
    worker = memory_worker.MemoryWorker({'records': Mock(), 'memories': Mock(),
        'memory_embeddings': Mock(), 'access_users': access}, settings)
    embedding_jobs.claim.return_value = {'_id': 'memory', 'user_uid': 'owner'}
    def process(jobs, current_service, row, stopped, authorized):
        assert jobs is embedding_jobs and current_service is service and authorized()
        worker.stop_event.set()
    monkeypatch.setattr(memory_worker, 'process_embedding_job', process)
    worker.run()
    analysis_jobs.claim.assert_not_called()
    embedding_jobs.claim.assert_called_once_with('owner')


def test_embedding_flag_defaults_off_and_accounts_are_explicit(monkeypatch):
    monkeypatch.delenv('MEMORY_EMBEDDING_ENABLED', raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().memory_embedding_enabled is False
        settings = Settings(admin_uid='owner', ai_allowed_uids=frozenset({'test', 'other'}),
                            atlas_chat_allowed_uids=frozenset({'test', 'not-ai'}))
        assert settings.embedding_user_uids == frozenset({'owner', 'test'})
    finally:
        get_settings.cache_clear()
