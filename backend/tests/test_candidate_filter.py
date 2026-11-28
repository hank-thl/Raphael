import json
from unittest.mock import Mock

import pytest

from app.services.candidate_filter import CandidateFilter, FilterError, FILTER_VERSION, INSTRUCTION
from app.services.gemini import GeminiError


def candidate(key='a',text='打算先買單程機票'):
    return {'memory_id':key*64,'revision':1,'text':text,'kind':['plan'],'topics':['旅行'],
            'status':'considering','evidence_type':'explicit','time_text':None}


def decision(key='a',label='direct',quote='打算先買單程機票'):
    return {'items':[{'memory_id':key*64,'relevance':label,'quote':quote,'reason':'只支持購票構想。'}],
            'answerability':'partial','explanation':'沒有購票完成的依據。'}


def test_bounded_single_call_contract_and_whitelisted_input():
    model=Mock();model.generate.return_value=decision()
    result=CandidateFilter(model).filter('已經買了嗎？',[candidate()])
    assert model.generate.call_count==1
    payload,schema,instruction=model.generate.call_args.args
    data=json.loads(payload)
    assert data=={'question':'已經買了嗎？','candidates':[candidate()]}
    assert 'required_memory_ids' not in payload and 'user_uid' not in payload and 'score' not in payload
    assert schema['additionalProperties'] is False and instruction==INSTRUCTION
    assert '不可信' in instruction
    assert result.answerability=='partial'
    assert FILTER_VERSION=='candidate-filter-v1-zh-v1'


@pytest.mark.parametrize('change',['unknown_id','duplicate_id','missing_id','invented_quote',
    'empty_quote','irrelevant_quote','no_support_sufficient','background_sufficient','extra_field','invalid_relevance','blank_reason'])
def test_invalid_model_results_rejected(change):
    candidates=[candidate(),candidate('b','沒有買票的紀錄')]
    output=decision()
    output['items'].append({'memory_id':'b'*64,'relevance':'irrelevant','quote':None,'reason':'不提供可確認資料。'})
    if change=='unknown_id':output['items'][0]['memory_id']='c'*64
    if change=='duplicate_id':output['items'][1]['memory_id']='a'*64
    if change=='missing_id':output['items'].pop()
    if change=='invented_quote':output['items'][0]['quote']='已經買票'
    if change=='empty_quote':output['items'][0]['quote']=' '
    if change=='irrelevant_quote':output['items'][1]['quote']='沒有'
    if change=='no_support_sufficient':
        output['items'][0].update(relevance='irrelevant',quote=None);output['answerability']='sufficient'
    if change=='background_sufficient':
        output['items'][0]['relevance']='background';output['answerability']='sufficient'
    if change=='extra_field':output['items'][0]['user_uid']='private'
    if change=='invalid_relevance':output['items'][0]['relevance']='maybe'
    if change=='blank_reason':output['items'][0]['reason']=' '
    model=Mock();model.generate.return_value=output
    with pytest.raises(FilterError,match='invalid_filter_result'):
        CandidateFilter(model).filter('question',candidates)


def test_all_irrelevant_can_be_insufficient_and_plan_can_be_relevant_without_confirmation():
    model=Mock();result=decision(label='irrelevant',quote=None);result['answerability']='insufficient'
    model.generate.return_value=result
    assert CandidateFilter(model).filter('洗頭多少錢？',[candidate()]).answerability=='insufficient'
    model.generate.return_value=decision()
    assert CandidateFilter(model).filter('已買票嗎？',[candidate()]).items[0].relevance=='direct'


@pytest.mark.parametrize('question,items', [('',[candidate()]),(' ',[candidate()]),
    ('x'*10001,[candidate()]),('q',[]),('q',[candidate()]*11),('q',[candidate(),candidate()]),
    ('q',[{**candidate(),'user_uid':'untrusted'}])])
def test_invalid_inputs_make_no_model_call(question,items):
    model=Mock()
    with pytest.raises(FilterError):CandidateFilter(model).filter(question,items)
    model.generate.assert_not_called()


def test_total_payload_limit_prevents_large_call():
    model=Mock();items=[candidate(key,'文'*4000) for key in 'abcde']
    with pytest.raises(FilterError,match='input_limit'):CandidateFilter(model).filter('q',items)
    model.generate.assert_not_called()


def test_provider_failure_propagates_once_without_raw_errors_or_retry():
    model=Mock();model.generate.side_effect=GeminiError('read_timeout')
    with pytest.raises(GeminiError,match='read_timeout'):CandidateFilter(model).filter('q',[candidate()])
    assert model.generate.call_count==1
