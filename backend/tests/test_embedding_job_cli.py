import json
from unittest.mock import Mock

import pytest

from app.services.memory_embeddings import MemoryEmbeddingError
from scripts import memory_embedding_jobs as cli


def test_status_is_scoped_and_does_not_expose_content_token_or_provider_error():
    collection = Mock()
    collection.find_one.return_value = {'_id': 'a' * 64, 'user_uid': 'owner', 'revision': 1,
        'content': 'private', 'embedding_processing': {'state': 'succeeded', 'token': 'secret', 'attempts': 1}}
    result = cli.inspect_job({'memories': collection}, 'owner', 'a' * 64)
    assert result['job']['state'] == 'succeeded' and not result['database_written']
    assert 'secret' not in json.dumps(result) and 'private' not in json.dumps(result)
    collection.find_one.assert_called_once_with({'_id': 'a' * 64, 'user_uid': 'owner', 'lifecycle': 'candidate'})
    collection.update_one.assert_not_called()


def test_latest_job_query_is_owner_scoped():
    collection = Mock()
    collection.find.return_value.sort.return_value.limit.return_value = [
        {'_id': 'id', 'user_uid': 'test', 'revision': 1, 'embedding_processing': {'state': 'pending'}}]
    assert cli.inspect_job({'memories': collection}, 'test')['job']['state'] == 'pending'
    collection.find.assert_called_once_with({'user_uid': 'test', 'lifecycle': 'candidate',
                                             'embedding_processing': {'$exists': True}})
    collection.find.return_value.sort.return_value.limit.assert_called_once_with(1)


def test_other_owner_job_never_returned():
    collection = Mock(); collection.find_one.return_value = {'user_uid': 'other'}
    with pytest.raises(MemoryEmbeddingError, match='job_not_found'):
        cli.inspect_job({'memories': collection}, 'owner', 'id')


def test_retry_revalidates_source_and_never_queues_old_memory(monkeypatch):
    collection = Mock()
    collection.find_one.return_value = {'_id': 'id', 'user_uid': 'owner', 'revision': 2}
    reader, jobs = Mock(), Mock()
    reader.get_eligible_many.return_value = [collection.find_one.return_value]
    jobs.retry.return_value = None
    monkeypatch.setattr(cli, 'MemoryVersionsService', lambda *args: reader)
    monkeypatch.setattr(cli, 'EmbeddingJobsRepository', lambda coll: jobs)
    db = {'memories': collection, 'records': Mock(), 'analyses': Mock()}
    with pytest.raises(MemoryEmbeddingError, match='job_not_retryable'):
        cli.inspect_job(db, 'owner', 'id', retry=True)
    jobs.retry.assert_called_once_with('owner', 'id', 2)
    reader.get_eligible_many.return_value = []
    jobs.retry.reset_mock()
    with pytest.raises(MemoryEmbeddingError, match='memory_unavailable'):
        cli.inspect_job(db, 'owner', 'id', retry=True)
    jobs.retry.assert_not_called()


def test_cli_offline_preview_and_retry_guard(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('no network'))
    monkeypatch.setattr(cli, 'get_settings', blocked)
    monkeypatch.setattr(cli, 'DatabaseConnection', blocked)
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)['provider_calls'] == 0
    with pytest.raises(SystemExit):
        cli.main(['--retry'])
    blocked.assert_not_called()
