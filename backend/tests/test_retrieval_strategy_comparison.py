from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services import embeddings
from scripts import compare_retrieval_strategies as comparison
from scripts import evaluate_memory_retrieval as evaluation
from test_private_retrieval_evaluation import plan, fake_client


def ranking():
    return [{'memory_id':mid,'score':score,'text':mid} for mid,score in [('a',.8),('b',.69),('c',.62),('d',.58),('e',.57),('f',.5)]]


@pytest.mark.parametrize('strategy,expected',[
    ('top3',['a','b','c']),('top5',['a','b','c','d','e']),('floor060',['a','b','c']),
    ('floor065',['a','b']),('floor070',['a']),('gap006',['a'])])
def test_selector_fixed_rules_and_budget(strategy,expected):
    assert [r['memory_id'] for r in comparison.select(ranking(),strategy,{})]==expected


def test_topic_scenario_uses_metadata_not_gold_and_does_not_require_high_score():
    selected=comparison.select(ranking(),'topic_top3',{'f':['心情']},['心情'])
    assert [r['memory_id'] for r in selected]==['a','b','c','f']
    assert comparison.select(ranking(),'topic_top3',{'f':['心情']})==ranking()[:3]
    selected=comparison.select(ranking(),'floor065_topic',{'d':['心情']},['心情'])
    assert [r['memory_id'] for r in selected]==['a','b','d']


def test_metadata_additions_never_exceed_budget_or_repeat_ids():
    selected=comparison.select(ranking(),'topic_top3',{r['memory_id']:['x'] for r in ranking()},['x'])
    assert len(selected)==5 and len({r['memory_id'] for r in selected})==5


def test_empty_and_no_positive_metrics_do_not_invent_recall():
    case={'id':'none','question':'unknown','required_memory_ids':[],'optional_memory_ids':[],'answerability':'unknown'}
    result=comparison.assess(case,[])
    assert result['required_recall'] is None and result['precision'] is None
    result=comparison.assess(case,ranking()[:1])
    assert result['precision']==0 and result['irrelevant_selected_ids']==['a']


def test_gap_rule_alone_does_not_reject_flat_irrelevant_results():
    rows=[{'memory_id':str(i),'score':.59-i*.01,'text':''} for i in range(6)]
    assert len(comparison.select(rows,'gap006',{}))==5
    assert comparison.select(rows,'floor060',{})==[]


def test_comparison_offline_and_labels_do_not_change_selection(plan,tmp_path,monkeypatch):
    path=tmp_path/'run.json'
    cache=evaluation.evaluate(plan,fake_client(),Mock(),path,evaluation.initial_state(plan))
    no_network=Mock(side_effect=AssertionError('no API'))
    monkeypatch.setattr(embeddings.GeminiEmbedder,'document',no_network)
    monkeypatch.setattr(embeddings.GeminiEmbedder,'query',no_network)
    result=comparison.compare(plan,cache)
    changed=deepcopy(plan)
    changed['cases'][0]['required_memory_ids']=plan['cases'][0]['irrelevant_memory_ids']
    changed['cases'][0]['irrelevant_memory_ids']=plan['cases'][0]['required_memory_ids']
    other=comparison.compare(changed,cache)
    for strategy in comparison.STRATEGIES:
        assert result['strategies'][strategy]['cases'][0]['selected']==other['strategies'][strategy]['cases'][0]['selected']
    assert not result['generalization_validated'] and result['selected_strategy'] is None
    assert result['provider_calls']==0 and not result['database_accessed']
    no_network.assert_not_called()


@pytest.mark.parametrize('case,topics',[('missing',['x']),('found',['x']),('found',[]),(None,['x'])])
def test_unknown_topic_or_scenario_rejected(plan,tmp_path,case,topics):
    cache=evaluation.evaluate(plan,fake_client(),Mock(),tmp_path/'run.json',evaluation.initial_state(plan))
    with pytest.raises(evaluation.EvaluationError):
        comparison.compare(plan,cache,coverage_case=case,topics=topics)


def test_partial_reports_are_not_compared(plan):
    with pytest.raises(evaluation.EvaluationError,match='completed_report_required'):
        comparison.compare(plan,evaluation.initial_state(plan))
