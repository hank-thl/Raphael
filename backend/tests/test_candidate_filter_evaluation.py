import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.schemas.candidate_filter import CandidateFilterResult
from app.services.gemini import GeminiError
from app.services.candidate_filter import CandidateFilter
from app.services.memory_answer import MemoryAnswerService
from scripts import evaluate_candidate_filter as evaluation
from scripts import evaluate_memory_retrieval as retrieval
from test_private_retrieval_evaluation import plan as retrieval_plan, fake_client


@pytest.fixture
def setup(retrieval_plan,tmp_path,monkeypatch):
    plan=retrieval_plan
    for m in plan['memories']:
        m['latest']['content'].update(kind=['event'],topics=['日常'],status='occurred',
                                     evidence={'type':'explicit'},time_text=None)
        row={'_id':m['memory_id'],'revision':1,'versions':[m['latest']]}
        m['fingerprint']=MemoryAnswerService.dependency(row)['fingerprint']
    snapshot_path=tmp_path/'source.json';snapshot=json.loads(snapshot_path.read_text())
    snapshot['memories']=plan['memories'];raw=json.dumps(snapshot).encode();snapshot_path.write_bytes(raw)
    dataset=json.loads(plan['path'].read_text());dataset['memory_map']=plan['memories']
    dataset['source_sha256']=hashlib.sha256(raw).hexdigest();plan['path'].write_text(json.dumps(dataset))
    plan=retrieval.load_plan(plan['path'])
    monkeypatch.setattr(evaluation,'PRIVATE_DIR',tmp_path)
    monkeypatch.setattr(evaluation,'DEFAULT_CASES',tmp_path/'cases.json')
    report=tmp_path/'evaluation-test.json'
    retrieval.evaluate(plan,fake_client(),Mock(),report,retrieval.initial_state(plan))
    jobs,report_hash=evaluation.prepare(plan,report)
    return plan,jobs,report_hash,report


def fake_service():
    service=Mock()
    def output(question,candidates,**kwargs):
        return CandidateFilterResult(items=[{
            'memory_id':c['memory_id'],'relevance':'direct' if c['text']=='document1' else 'irrelevant',
            'quote':c['text'] if c['text']=='document1' else None,'reason':'test'} for c in candidates],
            answerability='partial',explanation='test')
    service.filter.side_effect=output
    return service


def test_preview_never_loads_secrets_or_initializes_provider(setup,monkeypatch,capsys):
    no_calls=Mock(side_effect=AssertionError('network forbidden'))
    for name in ('get_settings','DatabaseConnection','GeminiExtractor'):
        monkeypatch.setattr(evaluation,name,no_calls)
    assert evaluation.main([])==0
    output=json.loads(capsys.readouterr().out)
    assert output['provider_calls']==0 and output['embedding_calls']==0
    assert output['planned_calls_max']==2 and output['candidates_per_question_max']==10
    no_calls.assert_not_called()


def test_evaluate_uses_recomputed_candidates_not_gold_and_saves_metrics(setup,tmp_path):
    plan,jobs,report_hash,_=setup;service=fake_service();verify=Mock()
    state=evaluation.evaluate(jobs,service,verify,tmp_path/'filter.json',evaluation.new_state(plan,report_hash,'test-model'))
    assert service.filter.call_count==2 and state['state']=='succeeded'
    assert state['call_attempts']==2 and state['semantic_passed'] is None
    for call in service.filter.call_args_list:
        assert set(call.args[1][0])=={'memory_id','revision','text','kind','topics','status','evidence_type','time_text'}
    assert set(state['completed']['found']['baselines'])=={'top3','top5','top10'}
    assert (tmp_path/'filter.json').stat().st_mode & 0o777==0o600
    assert verify.call_count>=6


def test_source_change_stops_before_provider(setup,tmp_path):
    plan,jobs,h,_=setup;service=fake_service()
    with pytest.raises(retrieval.EvaluationError,match='sources_changed'):
        evaluation.evaluate(jobs,service,Mock(side_effect=retrieval.EvaluationError('sources_changed')),
                            tmp_path/'filter.json',evaluation.new_state(plan,h,'model'))
    service.filter.assert_not_called()


def test_resume_reuses_completed_cases_but_rechecks_sources(setup,tmp_path):
    plan,jobs,h,_=setup;service=fake_service();first=service.filter.side_effect
    service.filter.side_effect=[first(jobs[0]['case']['question'],jobs[0]['candidates']),GeminiError('read_timeout')]
    path=tmp_path/'filter.json';state=evaluation.new_state(plan,h,'model')
    with pytest.raises(GeminiError):evaluation.evaluate(jobs,service,Mock(),path,state)
    assert len(state['completed'])==1 and service.filter.call_count==2
    resumed=evaluation.resume_state(path,plan,h,'model',jobs)
    verify=Mock();next_service=fake_service()
    result=evaluation.evaluate(jobs,next_service,verify,path,resumed)
    assert next_service.filter.call_count==1 and result['call_attempts']==3
    verify.assert_called()


def test_resume_rejects_other_model_or_changed_quote(setup,tmp_path):
    plan,jobs,h,_=setup;path=tmp_path/'filter.json'
    state=evaluation.evaluate(jobs,fake_service(),Mock(),path,evaluation.new_state(plan,h,'model'))
    with pytest.raises(retrieval.EvaluationError,match='resume_mismatch'):
        evaluation.resume_state(path,plan,h,'other-model',jobs)
    state['completed']['found']['decision']['items'][0]['quote']='unavailable text'
    retrieval.checkpoint(path,state)
    with pytest.raises(ValueError):evaluation.resume_state(path,plan,h,'model',jobs)


def test_cli_failure_saves_partial_without_exposing_private_values(setup,tmp_path,monkeypatch,capsys):
    plan,jobs,h,report=setup
    monkeypatch.setattr(evaluation,'get_settings',lambda:Settings(admin_uid='owner',mongodb_uri='secret-uri',
        gemini_api_key='secret-key',gemini_model='test-model'))
    connection=Mock(database={name:Mock() for name in ('records','analyses','memories')})
    monkeypatch.setattr(evaluation,'DatabaseConnection',lambda settings:connection)
    monkeypatch.setattr(evaluation,'validate_current',Mock())
    service=fake_service();service.filter.side_effect=GeminiError('read_timeout')
    monkeypatch.setattr(evaluation,'CandidateFilter',lambda extractor: service if extractor is not None else CandidateFilter(None))
    assert evaluation.main(['--run'])==1
    out=capsys.readouterr().out;data=json.loads(out)
    assert data['reason']=='read_timeout' and data['automatic_retry'] is False
    assert 'secret-key' not in out and 'secret-uri' not in out
    assert Path(data['report']).exists()
    assert service.filter.call_count==1
    connection.close.assert_called_once()
