import json
from unittest.mock import Mock

import pytest

from app.services.embeddings import EmbeddingError
from app.services.memory_embeddings import MemoryEmbeddingError
from scripts import backfill_memory_embeddings as cli


def reader_for(rows):
    reader = Mock()
    reader.memories.collection.find.return_value.sort.return_value.limit.return_value = rows
    return reader


def test_readonly_plan_bounds_scan_scopes_uid_and_never_generates():
    reader = reader_for([{'_id': 'a', 'user_uid': 'owner'}, {'_id': 'b', 'user_uid': 'owner'},
                         {'_id': 'c', 'user_uid': 'owner'}])
    service = Mock()
    service.snapshot.side_effect = lambda uid, mid: ({'revision': 1}, 'private text')
    service.current.side_effect = [None, {'vector': 'private vector'}]
    plan = cli.plan_batch(reader, service, 'owner', 2)
    assert plan['more_memories_outside_batch'] is True
    assert [r['action'] for r in plan['items']] == ['generate', 'reuse']
    assert cli.public_plan(plan)['planned_provider_calls_max'] == 1
    assert 'expected' not in cli.public_plan(plan)['items'][0]
    service.save.assert_not_called()
    reader.memories.collection.find.assert_called_once_with({'user_uid': 'owner', 'lifecycle': 'candidate'})
    reader.memories.collection.find.return_value.sort.return_value.limit.assert_called_once_with(3)


@pytest.mark.parametrize('error', [EmbeddingError('input_limit'), MemoryEmbeddingError('memory_unavailable')])
def test_unavailable_and_overlength_are_skipped(error):
    service = Mock()
    service.snapshot.side_effect = error
    plan = cli.plan_batch(reader_for([{'_id': 'a', 'user_uid': 'owner'}]), service, 'owner', 1)
    assert plan['items'][0]['action'] == 'skip'
    assert list(cli.save_batch(service, 'owner', plan))[0]['state'] == 'skipped'
    service.save.assert_not_called()


def test_foreign_owner_and_corrupt_cache_abort():
    service = Mock()
    with pytest.raises(MemoryEmbeddingError, match='owner_mismatch'):
        cli.plan_batch(reader_for([{'_id': 'a', 'user_uid': 'other'}]), service, 'owner', 1)
    service.snapshot.return_value = ({}, 'text')
    service.current.side_effect = MemoryEmbeddingError('invalid_cache')
    with pytest.raises(MemoryEmbeddingError, match='invalid_cache'):
        cli.plan_batch(reader_for([{'_id': 'a', 'user_uid': 'owner'}]), service, 'owner', 1)


def test_source_change_stops_before_save():
    service = Mock()
    service.recheck.side_effect = MemoryEmbeddingError('sources_changed')
    plan = {'items': [{'memory_id': 'a', 'action': 'generate', 'expected': {'revision': 1}}]}
    with pytest.raises(MemoryEmbeddingError, match='sources_changed'):
        list(cli.save_batch(service, 'owner', plan))
    service.save.assert_not_called()


def test_partial_completion_is_visible_and_no_automatic_retry():
    service = Mock()
    service.save.side_effect = [({}, False), EmbeddingError('rate_limited')]
    plan = {'items': [{'memory_id': mid, 'action': 'generate', 'expected': {}} for mid in ['a', 'b', 'c']]}
    steps = cli.save_batch(service, 'owner', plan)
    assert next(steps) == {'memory_id': 'a', 'state': 'saved'}
    with pytest.raises(EmbeddingError):
        next(steps)
    assert service.save.call_count == 2


def test_budget_counts_failed_attempts_and_rejects_extra_call():
    provider = Mock()
    provider.document.side_effect = EmbeddingError('network_error')
    counted = cli.CountedEmbedder(provider, 1)
    with pytest.raises(EmbeddingError):
        counted.document('text')
    with pytest.raises(MemoryEmbeddingError, match='call_budget_exceeded'):
        counted.document('text')
    assert counted.attempts == 1 and provider.document.call_count == 1


def test_cli_offline_default_and_explicit_save_guard(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('external access forbidden'))
    for name in ('get_settings', 'DatabaseConnection', 'GeminiEmbedder'):
        monkeypatch.setattr(cli, name, blocked)
    assert cli.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert not output['database_accessed'] and output['provider_calls'] == 0
    with pytest.raises(SystemExit):
        cli.main(['--save'])
    with pytest.raises(SystemExit):
        cli.main(['--limit', '21'])
    blocked.assert_not_called()
