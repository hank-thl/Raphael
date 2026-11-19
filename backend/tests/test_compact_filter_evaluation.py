import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.schemas.candidate_filter import CompactFilterResult
from app.services.candidate_filter import COMPACT_FILTER_VERSION
from scripts import evaluate_candidate_filter as evaluation
from scripts import evaluate_memory_retrieval as retrieval
from test_candidate_filter_evaluation import setup, fake_service
from test_private_retrieval_evaluation import plan as retrieval_plan


def compact_service():
    service=Mock()
    service.filter.side_effect=lambda q,cs,**kw:CompactFilterResult(
        keep_ids=[c['memory_id'] for c in cs if c['text']=='document1'],
        answerability='partial',limitation='Test limitation')
    return service


@pytest.fixture
def baseline(setup,tmp_path):
    plan,jobs,h,_=setup
    path=tmp_path/'filter-evaluation-baseline.json'
    state=evaluation.evaluate(jobs,fake_service(),Mock(),path,evaluation.new_state(plan,h,'model'))
    for item in state['completed'].values():
        item['elapsed_ms']=20000
        item['usage']={'promptTokenCount':1000,'candidatesTokenCount':1000,'totalTokenCount':3000}
    retrieval.checkpoint(path,state)
    return plan,jobs,h,path,state


def test_compact_preview_reuses_saved_baseline_without_loading_settings(baseline,monkeypatch,capsys):
    denied=Mock(side_effect=AssertionError('no external call'))
    monkeypatch.setattr(evaluation,'get_settings',denied)
    monkeypatch.setattr(evaluation,'DatabaseConnection',denied)
    monkeypatch.setattr(evaluation,'GeminiExtractor',denied)
    assert evaluation.main(['--variant','compact'])==0
    data=json.loads(capsys.readouterr().out)
    assert data['baseline_reused'] and data['baseline_provider_calls']==0
    assert data['provider_calls']==0 and data['prompt_version']==COMPACT_FILTER_VERSION
    denied.assert_not_called()


def test_historical_comparison_measures_outputs_usage_and_missing_values(baseline,tmp_path):
    plan,jobs,h,path,old=baseline
    new=evaluation.evaluate(jobs,compact_service(),Mock(),tmp_path/'compact.json',
                            evaluation.new_state(plan,h,'model',COMPACT_FILTER_VERSION))
    for item in new['completed'].values():
        item['elapsed_ms']=5000
        item['usage']={'promptTokenCount':1000,'candidatesTokenCount':100,'totalTokenCount':1500}
    compared=evaluation.compare_saved(new,old,jobs)
    assert compared['kind']=='historical_paired_comparison'
    assert compared['kept_ids_equal_cases']==2
    assert compared['median_elapsed_ms_before']==20000 and compared['median_elapsed_ms_after']==5000
    assert compared['usage_totals']['candidatesTokenCount']=={'before':2000,'after':200}
    assert compared['usage_totals']['thoughtsTokenCount']=={'before':None,'after':None}
    assert compared['cases'][0]['usage_delta']['thoughtsTokenCount'] is None
    assert compared['cases'][0]['usage_delta']['totalTokenCount']==-1500
    assert compared['cases'][0]['semantic_passed'] is None


def test_compact_resume_cannot_mix_detailed_schema_or_model(baseline,tmp_path):
    plan,jobs,h,path,old=baseline
    with pytest.raises(retrieval.EvaluationError,match='resume_mismatch'):
        evaluation.resume_state(path,plan,h,'model',jobs,COMPACT_FILTER_VERSION)
    new=evaluation.evaluate(jobs,compact_service(),Mock(),tmp_path/'compact.json',
                            evaluation.new_state(plan,h,'other',COMPACT_FILTER_VERSION))
    with pytest.raises(retrieval.EvaluationError,match='baseline_mismatch'):
        evaluation.compare_saved(new,old,jobs)


def test_candidate_mismatch_in_baseline_rejects(baseline):
    plan,jobs,h,path,old=baseline
    old['completed']['found']['candidate_ids']=[]
    retrieval.checkpoint(path,old)
    with pytest.raises(retrieval.EvaluationError,match='baseline_mismatch'):
        evaluation.detailed_baseline(plan,h,jobs,path)


def test_compact_cli_never_reruns_baseline_or_embeddings(baseline,tmp_path,monkeypatch,capsys):
    plan,jobs,h,path,old=baseline
    monkeypatch.setattr(evaluation,'get_settings',lambda:Settings(admin_uid='owner',mongodb_uri='secret-uri',
        gemini_api_key='secret-key',gemini_model='model'))
    connection=Mock(database={name:Mock() for name in ('records','analyses','memories')})
    monkeypatch.setattr(evaluation,'DatabaseConnection',lambda settings:connection)
    monkeypatch.setattr(evaluation,'validate_current',Mock())
    service=compact_service();monkeypatch.setattr(evaluation,'CompactCandidateFilter',lambda model:service)
    assert evaluation.main(['--variant','compact','--run'])==0
    data=json.loads(capsys.readouterr().out)
    assert service.filter.call_count==2
    result=json.loads(Path(data['report']).read_text())
    assert result['prompt_version']==COMPACT_FILTER_VERSION
    assert result['comparison']['baseline_provider_calls']==0
    assert result['comparison']['kept_ids_equal_cases']==2
    assert result['baseline_hash']
