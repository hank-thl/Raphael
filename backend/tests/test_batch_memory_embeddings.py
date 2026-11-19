from copy import deepcopy

import pytest

from app.services.memory_embeddings import MemoryEmbeddingError
from test_memory_embeddings import ctx
from test_memory_answer import setup as answer_setup


def prepare(ctx, count=5):
    service, reader, repo, provider, memory = ctx
    rows, cached = [], []
    for n in range(count):
        row = deepcopy(memory)
        row['_id'] = f'{n:064x}'
        reader.memories.get.return_value = row
        document, _ = service.save('owner', row['_id'])
        rows.append(row)
        cached.append(document)
    reader.memories.collection.find.return_value = rows
    repo.get_many.return_value = cached
    reader.reset_mock()
    repo.reset_mock()
    return service, reader, repo, rows, cached


def test_batch_matches_single_cache_documents_with_constant_query_count(ctx):
    service, reader, repo, rows, cached = prepare(ctx)
    result = service.current_many('owner', [r['_id'] for r in rows])
    assert result == {r['memory_id']: r for r in cached}
    assert reader.memories.collection.find.call_count == 2
    assert reader.get_eligible_many.call_count == 2
    assert repo.get_many.call_count == 1
    reader.memories.get.assert_not_called()
    repo.get.assert_not_called()
    query = reader.memories.collection.find.call_args.args[0]
    assert query['user_uid'] == 'owner' and query['lifecycle'] == 'candidate'
    assert set(query['_id']['$in']) == {r['_id'] for r in rows}


def test_foreign_rows_deleted_sources_and_stale_cache_are_excluded(ctx):
    service, reader, repo, rows, cached = prepare(ctx, 3)
    rows[0]['user_uid'] = 'other'
    reader.get_eligible_many.side_effect = lambda uid, batch: [r for r in batch if r['_id'] != rows[1]['_id']]
    rows[2]['versions'][-1]['content']['text'] += '補充'
    assert service.current_many('owner', [r['_id'] for r in rows]) == {}


def test_every_historical_content_change_invalidates_cache(ctx):
    service, reader, repo, rows, cached = prepare(ctx, 1)
    rows[0]['versions'].insert(0, deepcopy(rows[0]['versions'][0]))
    rows[0]['versions'][0]['content']['text'] = '舊版來源修正'
    assert service.current_many('owner', [rows[0]['_id']]) == {}


def test_change_during_cache_read_aborts_batch(ctx):
    service, reader, repo, rows, cached = prepare(ctx, 1)
    def read(uid, ids):
        rows[0]['revision'] += 1
        return cached
    repo.get_many.side_effect = read
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.current_many('owner', [rows[0]['_id']])


def test_deletion_during_cache_read_aborts_batch(ctx):
    service, reader, repo, rows, cached = prepare(ctx, 1)
    reader.get_eligible_many.side_effect = [rows, []]
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        service.current_many('owner', [rows[0]['_id']])


def test_wrong_owner_or_corrupt_cache_not_returned(ctx):
    service, reader, repo, rows, cached = prepare(ctx, 2)
    cached[0]['user_uid'] = 'other'
    cached[1]['fingerprint'] = 'corrupt'
    assert service.current_many('owner', [r['_id'] for r in rows]) == {}


def test_empty_batch_has_no_queries_and_overlimit_is_rejected(ctx):
    service, reader, repo, provider, memory = ctx
    assert service.current_many('owner', []) == {}
    reader.memories.collection.find.assert_not_called()
    repo.get_many.assert_not_called()
    with pytest.raises(MemoryEmbeddingError, match='invalid_batch'):
        service.current_many('owner', [str(i) for i in range(31)])
