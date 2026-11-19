import hashlib
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services.embeddings import DIMENSIONS, EmbeddingResult, PROFILE
from app.services.memory_embeddings import MemoryEmbeddingError
from app.services.memory_search import MemorySearchError, MemorySearchService, search_pipeline
from scripts import try_vector_search as cli


@pytest.fixture
def ctx():
    hit = {'_id': 'cache', 'user_uid': 'owner', 'memory_id': 'a' * 64, 'revision': 1,
        'fingerprint': 'fingerprint', 'profile': PROFILE, 'task': 'RETRIEVAL_DOCUMENT',
        'text_hash': 'hash', 'model': 'gemini-embedding-001', 'dimensions': DIMENSIONS, 'score': .9}
    current = {k: v for k, v in hit.items() if k != 'score'}
    current.update(vector=[1.] + [0.] * (DIMENSIONS - 1), created_at='time')
    collection, embeddings, provider = Mock(), Mock(), Mock()
    collection.aggregate.return_value = [hit]
    embeddings.current_many.return_value = {'a' * 64: current}
    provider.query.side_effect = lambda text: EmbeddingResult(
        tuple(current['vector']), hashlib.sha256(text.encode()).hexdigest(), 'RETRIEVAL_QUERY')
    return MemorySearchService(collection, embeddings, provider), collection, embeddings, provider, hit


def test_exact_search_prefilters_owner_and_profile_and_revalidates(ctx):
    service, collection, embeddings, provider, hit = ctx
    report = service.search('owner', '問題')
    assert report['results'] == [{'memory_id': 'a' * 64, 'revision': 1, 'fingerprint': 'fingerprint', 'score': .9}]
    pipeline = collection.aggregate.call_args.args[0]
    stage = pipeline[0]['$vectorSearch']
    assert stage['exact'] is True and 'numCandidates' not in stage
    assert stage['filter']['user_uid'] == {'$eq': 'owner'}
    assert stage['filter']['profile'] == {'$eq': PROFILE}
    assert stage['filter']['task'] == {'$eq': 'RETRIEVAL_DOCUMENT'}
    embeddings.current_many.assert_called_once_with('owner', ['a' * 64])
    embeddings.recheck_many.assert_called_once()
    embeddings.current.assert_not_called()
    embeddings.recheck.assert_not_called()
    assert 'vector' not in report['results'][0]
    assert provider.query.call_count == 1 and report['relevance_verified'] is False


@pytest.mark.parametrize('field,value', [('user_uid', 'other'), ('profile', 'other'),
    ('task', 'RETRIEVAL_QUERY'), ('revision', 2), ('fingerprint', 'old'), ('_id', 'old-cache')])
def test_other_user_or_stale_hit_is_excluded(ctx, field, value):
    service, collection, embeddings, provider, hit = ctx
    hit[field] = value
    result = service.search('owner', '問題')
    assert result['results'] == [] and result['rejected_count'] == 1
    if field in {'user_uid', 'profile', 'task'}:
        embeddings.current_many.assert_not_called()


def test_deleted_sources_and_missing_current_vector_are_excluded(ctx):
    service, collection, embeddings, provider, hit = ctx
    embeddings.current_many.return_value = {}
    assert service.search('owner', '問題')['results'] == []


def test_changes_after_search_abort_response(ctx):
    service, collection, embeddings, provider, hit = ctx
    embeddings.recheck_many.side_effect = MemoryEmbeddingError('sources_changed')
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.search('owner', '問題')


def test_empty_results_and_invalid_scores(ctx):
    service, collection, embeddings, provider, hit = ctx
    collection.aggregate.return_value = []
    assert service.search('owner', '問題')['results'] == []
    hit['score'] = float('nan')
    collection.aggregate.return_value = [hit]
    with pytest.raises(MemorySearchError, match='invalid_search_result'):
        service.search('owner', '問題')


def test_owner_and_limit_required_before_paid_call(ctx):
    service, collection, embeddings, provider, hit = ctx
    with pytest.raises(MemorySearchError, match='owner_required'):
        service.search('', '問題')
    with pytest.raises(MemorySearchError, match='invalid_limit'):
        service.search('owner', '問題', limit=100)
    provider.query.assert_not_called()


def test_index_definition_and_readiness():
    collection = Mock()
    expected = json.loads(cli.INDEX_FILE.read_text())
    index = {'type': 'vectorSearch', 'status': 'READY', 'queryable': True, 'latestDefinition': expected}
    collection.list_search_indexes.return_value = [index]
    cli.check_index(collection)
    index['queryable'] = False
    with pytest.raises(MemorySearchError, match='index_not_ready'):
        cli.check_index(collection)
    index['queryable'] = True
    index['latestDefinition'] = deepcopy(expected)
    index['latestDefinition']['fields'][0]['numDimensions'] = 1536
    with pytest.raises(MemorySearchError, match='index_definition_mismatch'):
        cli.check_index(collection)


def test_preview_is_offline(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('no external calls'))
    for name in ('get_settings', 'DatabaseConnection', 'GeminiEmbedder'):
        monkeypatch.setattr(cli, name, blocked)
    assert cli.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['provider_calls'] == 0 and report['database_accessed'] is False
    blocked.assert_not_called()


def test_query_profile_mismatch_never_queries_atlas(ctx):
    service, collection, embeddings, provider, hit = ctx
    provider.query.side_effect = lambda text: EmbeddingResult((1.,) * DIMENSIONS, 'wrong', 'RETRIEVAL_DOCUMENT')
    with pytest.raises(MemorySearchError, match='embedding_mismatch'):
        service.search('owner', '問題')
    collection.aggregate.assert_not_called()


def test_duplicate_memory_is_returned_once(ctx):
    service, collection, embeddings, provider, hit = ctx
    collection.aggregate.return_value = [hit, deepcopy(hit)]
    result = service.search('owner', '問題')
    assert len(result['results']) == 1 and result['rejected_count'] == 1


def test_unready_index_fails_before_embedding(monkeypatch, capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(cli, 'get_settings', lambda: SimpleNamespace(
        admin_uid='owner', mongodb_uri='fake', gemini_api_key='fake'))
    connection = Mock()
    connection.database.__getitem__ = Mock(return_value=Mock())
    # A dict avoids needing a MagicMock for database indexing.
    collection = Mock()
    collection.list_search_indexes.return_value = []
    connection.database = {'memory_embeddings': collection}
    monkeypatch.setattr(cli, 'DatabaseConnection', lambda settings: connection)
    provider = Mock(side_effect=AssertionError('no paid call'))
    monkeypatch.setattr(cli, 'GeminiEmbedder', provider)
    assert cli.main(['--run']) == 1
    assert json.loads(capsys.readouterr().out)['reason'] == 'index_not_ready'
    provider.assert_not_called()
    connection.close.assert_called_once()
