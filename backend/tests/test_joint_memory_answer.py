import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services.joint_memory_answer import JointMemoryAnswerExtractor
from app.services.memory_answer import AnswerError
from scripts import evaluate_retrieval_answers as trial
from scripts import evaluate_memory_retrieval as retrieval
from test_memory_answer import setup as answer_setup
from test_retrieval_answer_trial import context, snapshot


def joint_output(payload, *args, **kwargs):
    data = json.loads(payload)
    missing = data['question'] == 'expense-missing'
    sufficient = data['question'] == 'exercise'
    ids = [] if missing else [data['memories'][0]['memory_id']]
    return {'keep_ids': ids, 'answerability': 'sufficient' if sufficient else 'insufficient',
            'limitation': None if sufficient else '缺少相關事件或持續時間。',
            'answer': {'passages': [{'text': '沒有相關消費紀錄。' if missing else '你記錄過上陶藝課很開心。',
                'basis': 'limitation' if missing else 'explicit',
                'sources': [] if missing else [{'memory_id': ids[0], 'revision': 1}]}],
                'insufficient_information': not sufficient}}


def test_joint_single_call_preserves_all_dependencies_and_source_checks(context, tmp_path):
    plan, vectors, reader, embedder, filters, provider = context
    other = deepcopy(reader.memories.collection.find.return_value[0])
    other['_id'] = 'b' * 64
    reader.memories.collection.find.return_value.append(other)
    plan['memories'].append(snapshot(other))
    vectors['document:' + other['_id']] = vectors['document:' + 'a' * 64]
    provider.generate.side_effect = joint_output
    verify = Mock()
    path = tmp_path / 'joint.json'
    result = trial.evaluate(plan, vectors, reader, 'owner', embedder, filters, provider,
        verify, path, trial.new_state(plan, 'h', 'model', case_ids=('mood',), joint=True))
    assert result['call_attempts'] == 2
    filters.filter.assert_not_called()
    assert provider.generate.call_count == 1
    stage = result['cases']['mood']['stages']['joint_answer']
    assert len(stage['inputs']) == 2 and len(stage['joint']['keep_ids']) == 1
    timing = result['cases']['mood']['timings_this_attempt']
    assert timing['source_verify_calls'] == 4 and verify.call_count == 6
    assert timing['selected_collect_calls'] == 2 and timing['answer_provider_calls'] == 1
    restored = trial.resume_state(path, plan, 'h', 'model', vectors, case_ids=('mood',), joint=True)
    trial.evaluate(plan, vectors, reader, 'owner', embedder, filters, provider, verify, path, restored)
    assert provider.generate.call_count == 1 and embedder.query.call_count == 1
    with pytest.raises(retrieval.EvaluationError, match='resume_mismatch'):
        trial.resume_state(path, plan, 'h', 'model', vectors, case_ids=('mood',))


def test_joint_no_evidence_does_not_fabricate_references(context, tmp_path):
    plan, vectors, reader, embedder, filters, provider = context
    provider.generate.side_effect = joint_output
    result = trial.evaluate(plan, vectors, reader, 'owner', embedder, filters, provider,
        Mock(), tmp_path / 'missing.json',
        trial.new_state(plan, 'h', 'model', case_ids=('expense-missing',), joint=True))
    stage = result['cases']['expense-missing']['stages']['joint_answer']
    assert stage['joint']['keep_ids'] == []
    assert stage['answer']['passages'][0]['sources'] == []
    assert not result['database_written']


def test_joint_rechecks_sources_after_generation(context, tmp_path):
    plan, vectors, reader, embedder, filters, provider = context
    def change_source(payload, *args, **kwargs):
        output = joint_output(payload)
        reader.memories.collection.find.return_value = []
        return output
    provider.generate.side_effect = change_source
    with pytest.raises(AnswerError, match='sources_changed'):
        trial.evaluate(plan, vectors, reader, 'owner', embedder, filters, provider,
            Mock(), tmp_path / 'changed.json',
            trial.new_state(plan, 'h', 'model', case_ids=('mood',), joint=True))
    assert provider.generate.call_count == 1
    filters.filter.assert_not_called()


@pytest.mark.parametrize('mutation', ['unknown_id', 'unkept_reference', 'bad_revision', 'flag'])
def test_joint_rejects_invalid_selection_or_citation(mutation):
    payload = json.dumps({'question': 'exercise', 'memories': [{
        'memory_id': 'a' * 64, 'revision': 1, 'history': [],
        'latest': {'revision': 1, 'evidence': {'type': 'explicit'}}}]})
    output = joint_output(payload)
    if mutation == 'unknown_id':
        output['keep_ids'] = ['b' * 64]
    elif mutation == 'unkept_reference':
        output.update(keep_ids=[], answerability='insufficient', limitation='缺少依據')
        output['answer']['insufficient_information'] = True
    elif mutation == 'bad_revision':
        output['answer']['passages'][0]['sources'][0]['revision'] = 2
    else:
        output['answer']['insufficient_information'] = True
    provider = Mock()
    provider.generate.return_value = output
    with pytest.raises(AnswerError, match='invalid_joint_answer'):
        JointMemoryAnswerExtractor(provider).generate(payload, {}, 'instruction')
    assert provider.generate.call_count == 1


def test_joint_preview_has_two_calls_and_never_connects(context, monkeypatch, capsys):
    plan, vectors, *_ = context
    monkeypatch.setattr(trial, 'load_plan', lambda path: plan)
    monkeypatch.setattr(trial, 'find_report', lambda plan: 'report')
    monkeypatch.setattr(trial, 'load_resume', lambda path, plan: {'state': 'succeeded', 'vectors': vectors})
    monkeypatch.setattr(trial, 'read_json', lambda path: ({}, 'hash'))
    blocked = Mock(side_effect=AssertionError('network forbidden'))
    for name in ('get_settings', 'DatabaseConnection', 'GeminiExtractor', 'GeminiEmbedder'):
        monkeypatch.setattr(trial, name, blocked)
    assert trial.main(['--mode', 'joint', '--case', 'mood']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['planned_calls_max'] == 2 and report['filter_calls_max'] == 0
    assert report['provider_calls'] == 0
    blocked.assert_not_called()
