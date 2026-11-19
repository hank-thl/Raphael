import hashlib
from unittest.mock import Mock

import pytest

from app.services.embeddings import DIMENSIONS, EmbeddingError, EmbeddingResult
from app.services.memory_embeddings import MemoryEmbeddingError, MemoryEmbeddingService
from test_memory_answer import setup as answer_setup


@pytest.fixture
def ctx(answer_setup):
    _, reader, _, memory = answer_setup
    reader.memories.get.return_value = memory
    reader.get_eligible_many.side_effect = lambda uid, rows: rows
    documents = {}
    repo = Mock()
    repo.get.side_effect = lambda uid, identifier: documents.get((uid, identifier))
    repo.create_once.side_effect = lambda row: documents.setdefault((row['user_uid'], row['_id']), row)
    embedder = Mock()
    embedder.document.side_effect = lambda text: EmbeddingResult(
        (1.0,) + (0.0,) * (DIMENSIONS - 1), hashlib.sha256(text.encode()).hexdigest(), 'RETRIEVAL_DOCUMENT')
    return MemoryEmbeddingService(reader, repo, embedder), reader, repo, embedder, memory


def test_save_and_reuse_without_another_paid_call(ctx):
    service, reader, repo, provider, memory = ctx
    first, reused = service.save('owner', memory['_id'])
    assert not reused and len(first['vector']) == DIMENSIONS
    second, reused = service.save('owner', memory['_id'])
    assert reused and first == second and provider.document.call_count == 1
    assert service.current('owner', memory['_id']) == first
    assert repo.create_once.call_count == 1
    assert reader.memories.get.call_args.args[0] == 'owner'


@pytest.mark.parametrize('change', ['uid', 'deleted', 'lifecycle'])
def test_ineligible_owner_or_source_never_returns_cached_vector(ctx, change):
    service, reader, repo, provider, memory = ctx
    service.save('owner', memory['_id'])
    if change == 'uid':
        memory['user_uid'] = 'someone-else'
    elif change == 'lifecycle':
        memory['lifecycle'] = 'disabled'
    else:
        reader.get_eligible_many.side_effect = lambda uid, rows: []
    with pytest.raises(MemoryEmbeddingError, match='memory_unavailable'):
        service.current('owner', memory['_id'])
    assert provider.document.call_count == 1


@pytest.mark.parametrize('change', ['revision', 'text', 'evidence'])
def test_revision_or_fingerprint_change_invalidates_old_cache(ctx, change):
    service, reader, repo, provider, memory = ctx
    old, _ = service.save('owner', memory['_id'])
    if change == 'revision':
        memory['revision'] += 1
    elif change == 'text':
        memory['versions'][-1]['content']['text'] += '新的補充'
    else:
        memory['versions'][-1]['content']['evidence']['type'] = 'contextual'
    assert service.current('owner', memory['_id']) is None
    new, reused = service.save('owner', memory['_id'])
    assert not reused and old['_id'] != new['_id'] and provider.document.call_count == 2


def test_source_changes_during_generation_prevent_insert(ctx):
    service, reader, repo, provider, memory = ctx
    original = provider.document.side_effect
    def generate(text):
        result = original(text)
        reader.get_eligible_many.side_effect = lambda uid, rows: []
        return result
    provider.document.side_effect = generate
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.save('owner', memory['_id'])
    repo.create_once.assert_not_called()


def test_source_changes_during_insert_are_not_retrievable(ctx):
    service, reader, repo, provider, memory = ctx
    original = repo.create_once.side_effect
    def insert(row):
        result = original(row)
        reader.get_eligible_many.side_effect = lambda uid, rows: []
        return result
    repo.create_once.side_effect = insert
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.save('owner', memory['_id'])
    with pytest.raises(MemoryEmbeddingError, match='memory_unavailable'):
        service.current('owner', memory['_id'])


def test_over_budget_does_not_call_provider_or_write(ctx):
    service, reader, repo, provider, memory = ctx
    memory['versions'][-1]['content']['text'] = '字' * 601
    with pytest.raises(EmbeddingError):
        service.save('owner', memory['_id'])
    provider.document.assert_not_called()
    repo.create_once.assert_not_called()


def test_mismatched_embedding_metadata_does_not_write(ctx):
    service, reader, repo, provider, memory = ctx
    provider.document.side_effect = lambda text: EmbeddingResult((1.,) * DIMENSIONS, 'wrong', 'RETRIEVAL_QUERY')
    with pytest.raises(MemoryEmbeddingError, match='embedding_mismatch'):
        service.save('owner', memory['_id'])
    repo.create_once.assert_not_called()


def test_corrupt_cache_is_rejected_without_regeneration(ctx):
    service, reader, repo, provider, memory = ctx
    document, _ = service.save('owner', memory['_id'])
    document['vector'] = [2.] * DIMENSIONS
    with pytest.raises(MemoryEmbeddingError, match='invalid_cache'):
        service.current('owner', memory['_id'])
    assert provider.document.call_count == 1


def test_cli_preview_never_opens_settings_or_network(monkeypatch, capsys):
    from scripts import save_memory_embedding as cli
    blocked = Mock(side_effect=AssertionError('external access forbidden'))
    for name in ('get_settings', 'DatabaseConnection', 'GeminiEmbedder'):
        monkeypatch.setattr(cli, name, blocked)
    assert cli.main(['--memory-id', 'a' * 64]) == 0
    assert '"database_accessed": false' in capsys.readouterr().out
    blocked.assert_not_called()


def test_repository_duplicate_insert_is_owner_scoped():
    from pymongo.errors import DuplicateKeyError
    from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
    collection = Mock()
    document = {'_id': 'id', 'user_uid': 'owner'}
    collection.insert_one.side_effect = DuplicateKeyError('duplicate')
    collection.find_one.return_value = document
    assert MemoryEmbeddingsRepository(collection).create_once(document) == document
    collection.find_one.assert_called_once_with({'_id': 'id', 'user_uid': 'owner'})


def test_backfill_plan_cannot_silently_embed_changed_content(ctx):
    service, reader, repo, provider, memory = ctx
    expected, _ = service.snapshot('owner', memory['_id'])
    memory['versions'][-1]['content']['text'] += '補充'
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.save('owner', memory['_id'], expected_snapshot=expected)
    provider.document.assert_not_called()
    repo.create_once.assert_not_called()
