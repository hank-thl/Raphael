import json
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.services.gemini import GeminiError
from app.services.memory_answer import MAX_INPUT_CHARS, MAX_MEMORIES, INSTRUCTION
from scripts import evaluate_answers as evaluation


def selected(name):
    return next(row for row in evaluation.load_cases() if row['id'] == name)


def test_frozen_baseline_and_synthetic_case_bounds():
    prompt, schema, meta = evaluation.baseline()
    assert meta['prompt_version'] == 'memory-answer-analysis-v1.3'
    assert meta['contract_version'] == 'memory-answer-v1'
    assert 'general' not in schema['$defs']['AnswerPassage']['properties']['basis']['enum']
    assert len(INSTRUCTION) <= len(prompt)
    cases = evaluation.load_cases()
    assert len(cases) == 10
    assert {row['language'] for row in cases} == {'zh-TW', 'ja', 'en', 'th'}
    for case in cases:
        payload, memories = evaluation.prepare(case)
        assert len(memories) <= MAX_MEMORIES
        assert len(json.dumps(payload, ensure_ascii=False)) <= MAX_INPUT_CHARS
        assert payload['previous_conversation'] == []
        assert 'review_checks' not in payload
        assert 'user_uid' not in json.dumps(payload)


def test_ab_uses_identical_payload_and_measures_actual_usage_without_semantic_auto_pass():
    calls = []
    def generate(payload, schema, prompt, *, usage_callback):
        calls.append(json.loads(payload))
        usage_callback({'promptTokenCount': 100 if len(calls) == 1 else 90, 'totalTokenCount': 120})
        mid = calls[-1]['memories'][0]['memory_id']
        return {'passages': [{'text': '共500元。', 'basis': 'explicit', 'sources': [{'memory_id': mid, 'revision': 1}]}],
                'insufficient_information': False}
    report = evaluation.compare(selected('fact'), Mock(generate=generate))
    assert calls[0] == calls[1]
    assert report['baseline']['contract_valid'] is True
    assert report['candidate']['contract_valid'] is True
    assert report['token_delta']['promptTokenCount'] == -10
    assert report['token_delta']['candidatesTokenCount'] is None
    assert report['human_review_required'] is True and report['semantic_passed'] is None


def test_old_general_contract_failure_is_distinct_from_new_valid_contract():
    model = Mock()
    model.generate.return_value = {'passages': [{'text': '一般知識。', 'basis': 'general', 'sources': []}],
                                   'insufficient_information': False}
    report = evaluation.compare(selected('general'), model)
    assert report['baseline']['error'] == 'contract_validation_failed'
    assert report['candidate']['contract_valid'] is True
    assert report['semantic_passed'] is None
    assert all(value is None for value in report['token_delta'].values())


def test_provider_error_stops_without_retrying_or_running_candidate():
    model = Mock()
    model.generate.side_effect = GeminiError('read_timeout')
    report = evaluation.compare(selected('fact'), model)
    assert report['stopped'] is True
    assert report['baseline']['error'] == 'read_timeout'
    assert 'candidate' not in report
    assert model.generate.call_count == 1


def test_preview_never_initializes_provider_or_loads_secrets(monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError('No provider initialization'))
    monkeypatch.setattr(evaluation, 'GeminiExtractor', factory)
    monkeypatch.setattr(evaluation, 'get_settings', Mock(side_effect=AssertionError('No settings needed')))
    assert evaluation.main(['--case', 'all']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['planned_calls'] == 20 and output['provider_calls'] == 0
    factory.assert_not_called()


def test_run_requires_config_before_calls(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(evaluation, 'get_settings', lambda: Settings())
    monkeypatch.setattr(evaluation, 'GeminiExtractor', factory)
    assert evaluation.main(['--run']) == 1
    factory.assert_not_called()


def test_candidate_only_uses_one_call_and_does_not_invent_comparison():
    model = Mock()
    model.generate.return_value = {'passages': [{'text': '一般知識。', 'basis': 'general', 'sources': []}],
                                   'insufficient_information': False}
    result = evaluation.compare(selected('general'), model, candidate_only=True)
    assert model.generate.call_count == 1
    assert result['comparison'] is False and 'baseline' not in result
    assert all(value is None for value in result['token_delta'].values())
    assert result['semantic_passed'] is None


def test_candidate_only_preview_reports_correct_call_budget(monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError('No calls'))
    monkeypatch.setattr(evaluation, 'GeminiExtractor', factory)
    assert evaluation.main(['--case', 'th', '--candidate-only']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['provider_calls'] == 0 and report['planned_calls'] == 1
    factory.assert_not_called()
