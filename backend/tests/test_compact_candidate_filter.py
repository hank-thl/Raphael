import json
from unittest.mock import Mock

import pytest

from app.services.candidate_filter import (
    CompactCandidateFilter, COMPACT_FILTER_VERSION, COMPACT_INSTRUCTION,
    FILTER_VERSION, INSTRUCTION, FilterError,
)
from app.services.gemini import GeminiError
from test_candidate_filter import candidate


def output():
    return {'keep_ids':['a'*64],'answerability':'partial','limitation':'只有購票打算，沒有完成紀錄。'}


def test_compact_output_only_and_detailed_instructions_remain_unchanged():
    model=Mock();model.generate.return_value=output()
    value=CompactCandidateFilter(model).filter('已買票嗎？',[candidate()])
    payload,schema,prompt=model.generate.call_args.args
    assert json.loads(payload)=={'question':'已買票嗎？','candidates':[candidate()]}
    assert set(schema['properties'])=={'keep_ids','answerability','limitation'}
    assert 'items' not in value.model_dump() and 'quote' not in value.model_dump()
    assert '在內部使用以下相關性判準' in prompt
    assert 'direct／background 的 quote 必須' not in prompt and 'irrelevant 的 quote=null' not in prompt
    assert '在 limitation 簡短指出' in prompt
    assert '每個候選必須恰好分類一次：' in INSTRUCTION
    assert 'irrelevant 的 quote=null' in INSTRUCTION
    assert FILTER_VERSION=='candidate-filter-v1-zh-v1' and COMPACT_FILTER_VERSION=='candidate-filter-compact-v1-zh-v1'
    assert len(prompt)<len(INSTRUCTION)+100


@pytest.mark.parametrize('change',['unknown','duplicate','extra_field','blank_limitation',
    'empty_partial','empty_sufficient','sufficient_limitation','missing_limitation','bad_answerability','too_long'])
def test_compact_contract_safety(change):
    raw=output()
    if change=='unknown':raw['keep_ids']=['b'*64]
    if change=='duplicate':raw['keep_ids']=['a'*64,'a'*64]
    if change=='extra_field':raw['reasons']=['private']
    if change=='blank_limitation':raw['limitation']=' '
    if change=='empty_partial':raw['keep_ids']=[]
    if change=='empty_sufficient':raw.update(keep_ids=[],answerability='sufficient',limitation=None)
    if change=='sufficient_limitation':raw['answerability']='sufficient'
    if change=='missing_limitation':raw['limitation']=None
    if change=='bad_answerability':raw['answerability']='yes'
    if change=='too_long':raw['limitation']='文'*181
    model=Mock();model.generate.return_value=raw
    with pytest.raises(FilterError,match='invalid_filter_result'):
        CompactCandidateFilter(model).filter('question',[candidate()])


def test_empty_irrelevant_and_relevant_insufficient_are_distinct_valid_cases():
    model=Mock();model.generate.return_value={'keep_ids':[],'answerability':'insufficient','limitation':'沒有費用資料。'}
    assert CompactCandidateFilter(model).filter('費用？',[candidate()]).keep_ids==[]
    model.generate.return_value={'keep_ids':['a'*64],'answerability':'insufficient','limitation':'沒有完成紀錄。'}
    assert CompactCandidateFilter(model).filter('已買嗎？',[candidate()]).keep_ids==['a'*64]
    model.generate.return_value={'keep_ids':['a'*64],'answerability':'sufficient','limitation':None}
    assert CompactCandidateFilter(model).filter('購票打算？',[candidate()]).answerability=='sufficient'


def test_input_budget_and_provider_failure_do_not_retry():
    model=Mock()
    with pytest.raises(FilterError):CompactCandidateFilter(model).filter('q',[candidate()]*11)
    model.generate.assert_not_called()
    model.generate.side_effect=GeminiError('read_timeout')
    with pytest.raises(GeminiError):CompactCandidateFilter(model).filter('q',[candidate()])
    assert model.generate.call_count==1
