import json
from unittest.mock import Mock

import pytest

from app.schemas.candidate_filter import CompactFilterResult
from app.services.memory_answer import ANSWER_PROMPT_VERSION, AnswerError, INSTRUCTION
from scripts import evaluate_memory_retrieval as retrieval
from scripts import evaluate_retrieval_answers as trial
from test_retrieval_answer_trial import context
from test_memory_answer import setup as answer_setup


def test_assessment_is_non_evidence_context_and_baseline_prompt_is_preserved():
    provider=Mock();usage={};metrics={}
    decision=CompactFilterResult(keep_ids=['a'*64],answerability='insufficient',limitation='未知持續時間。')
    payload=json.dumps({'question':'情緒變化？','memories':[{'text':'孤單但不想處理'}]},ensure_ascii=False)
    adapter=trial.MeasuredExtractor(provider,usage,assessment=decision,metrics=metrics)
    adapter.generate(payload,{},INSTRUCTION)
    sent,schema,prompt=provider.generate.call_args.args
    data=json.loads(sent)
    assert data['memories']==json.loads(payload)['memories']
    assert data['retrieval_assessment']=={'answerability':'insufficient','limitation':'未知持續時間。','not_personal_evidence':True}
    assert prompt==INSTRUCTION+trial.SCOPE_INSTRUCTION
    assert metrics['answer_provider_calls']==1 and metrics['answer_provider_ms']>=0
    assert ANSWER_PROMPT_VERSION=='memory-answer-strategies-v2.3-zh-v1'
    baseline=Mock();trial.MeasuredExtractor(baseline,{}).generate(payload,{},INSTRUCTION)
    assert baseline.generate.call_args.args==(payload,{},INSTRUCTION)


def test_augmented_payload_obeys_existing_budget_before_provider(monkeypatch):
    monkeypatch.setattr(trial,'ANSWER_INPUT_LIMIT',20)
    provider=Mock();decision=CompactFilterResult(keep_ids=[],answerability='insufficient',limitation='沒有資料')
    with pytest.raises(AnswerError,match='input_limit'):
        trial.MeasuredExtractor(provider,{},assessment=decision).generate('{}',{},INSTRUCTION)
    provider.generate.assert_not_called()


def test_one_case_mode_measures_work_without_skipping_validation(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    state=trial.new_state(plan,'h','model',scoped=True,case_ids=('mood',))
    verify=Mock()
    result=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,verify,tmp_path/'scope.json',state)
    assert result['call_attempts']==3 and set(result['cases'])=={'mood'}
    assert result['answer_prompt']==trial.SCOPED_ANSWER_PROMPT
    item=result['cases']['mood'];timing=item['timings_this_attempt']
    assert timing['source_verify_calls']==6 and verify.call_count==8
    assert timing['checkpoint_calls']==6
    assert timing['selected_collect_calls']==2 and timing['answer_record_read_calls']==1
    assert timing['answer_provider_calls']==1
    assert all(v>=0 for k,v in timing.items() if k.endswith('_ms'))
    assert item['wall_elapsed_ms'] is not None
    payload=json.loads(extractor.generate.call_args.args[0])
    assert payload['retrieval_assessment']['answerability']=='insufficient'
    assert payload['memories'] and payload['memories'][0]['latest']['evidence']['sources']
    # The backend does not turn the filter's insufficient flag into a forced refusal.
    assert item['stages']['answer']['answer']['passages']
    assert not result['database_written']


def test_resume_cannot_mix_scope_or_case_selection(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    path=tmp_path/'scope.json'
    state=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,
                         trial.new_state(plan,'h','model',True,('mood',)))
    with pytest.raises(retrieval.EvaluationError,match='resume_mismatch'):
        trial.resume_state(path,plan,'h','model',vectors)
    with pytest.raises(retrieval.EvaluationError,match='resume_mismatch'):
        trial.resume_state(path,plan,'h','model',vectors,True,trial.CASE_IDS)
    restored=trial.resume_state(path,plan,'h','model',vectors,True,('mood',))
    trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,restored)
    assert embedder.query.call_count==1 and filter_service.filter.call_count==1 and extractor.generate.call_count==1


def test_legacy_baseline_report_remains_readable(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    path=tmp_path/'old.json'
    state=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,
                         trial.new_state(plan,'h','model'))
    state.pop('scope_enabled');state.pop('case_ids')
    retrieval.checkpoint(path,state)
    assert trial.resume_state(path,plan,'h','model',vectors)['answer_prompt']==ANSWER_PROMPT_VERSION


def test_scoped_single_case_preview_is_offline(context,monkeypatch,capsys):
    plan,vectors,*_=context
    monkeypatch.setattr(trial,'load_plan',lambda path:plan)
    monkeypatch.setattr(trial,'find_report',lambda plan:'report')
    monkeypatch.setattr(trial,'load_resume',lambda path,plan:{'state':'succeeded','vectors':vectors})
    monkeypatch.setattr(trial,'read_json',lambda path:({},'hash'))
    blocked=Mock(side_effect=AssertionError('network forbidden'))
    for name in ('get_settings','DatabaseConnection','GeminiExtractor','GeminiEmbedder'):
        monkeypatch.setattr(trial,name,blocked)
    assert trial.main(['--case','mood'])==0
    data=json.loads(capsys.readouterr().out)
    assert data['planned_calls_max']==3 and data['mode_variant']=='scoped'
    assert data['answer_prompt']==trial.SCOPED_ANSWER_PROMPT and data['provider_calls']==0
    blocked.assert_not_called()
