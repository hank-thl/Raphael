from unittest.mock import Mock
import json

import pytest
from pydantic import ValidationError

from app.schemas.matching import MatchDecision
from app.services.gemini import GeminiError
from scripts import diagnose_memory_match as cli


def test_old_saved_decisions_remain_readable_without_note():
    result = MatchDecision.model_validate({'action': 'new', 'memory_id': None, 'merged': None})
    assert result.decision_note is None


@pytest.mark.parametrize('note', ['', '   ', 'x' * 301])
def test_invalid_or_unbounded_note_is_rejected(note):
    with pytest.raises(ValidationError):
        MatchDecision.model_validate({'action': 'new', 'memory_id': None, 'merged': None, 'decision_note': note})


def test_diagnostic_makes_one_call_and_never_forces_expected_action():
    provider = Mock()
    provider.generate.return_value = {'action': 'new', 'memory_id': None, 'merged': None,
        'decision_note': '範例判斷：未建立同一經歷的連結。'}
    result = cli.diagnose(provider)
    assert provider.generate.call_count == 1 and result['expectation_met'] is False
    assert result['decision']['action'] == 'new' and result['note_available']
    assert result['new_judgment_not_original_call_explanation'] is True
    assert not result['database_accessed'] and not result['database_written']
    payload = json.loads(provider.generate.call_args.args[0])
    assert len(payload['options']) == 1 and len(payload['raw_records']) == 2


def test_diagnostic_rejects_fabricated_quote_without_retry():
    provider = Mock()
    incoming, options, raw = cli.fixture()
    merged = {**incoming, 'evidence': {'type': 'contextual', 'sources': [
        {'record_id': row['record_id'], 'quote': row['content']} for row in raw]}}
    merged['evidence']['sources'][0]['quote'] = '醫生因為爆痘而開藥'
    provider.generate.return_value = {'action': 'supplement', 'memory_id': options[0]['memory_id'],
        'merged': merged, 'decision_note': '測試說明'}
    with pytest.raises(GeminiError, match='invalid_matching_response'):
        cli.diagnose(provider)
    assert provider.generate.call_count == 1


def test_preview_never_loads_key_or_calls_provider(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('no network'))
    monkeypatch.setattr(cli, 'get_settings', blocked)
    monkeypatch.setattr(cli, 'GeminiExtractor', blocked)
    assert cli.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['provider_calls'] == 0 and not report['database_accessed']
    blocked.assert_not_called()
