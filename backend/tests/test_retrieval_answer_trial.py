import hashlib
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.schemas.candidate_filter import CompactFilterResult
from app.services.embeddings import DIMENSIONS, EmbeddingResult
from app.services.gemini import GeminiError
from app.services.memory_answer import AnswerError, MemoryAnswerService
from scripts import evaluate_retrieval_answers as trial
from scripts import evaluate_memory_retrieval as retrieval
from test_memory_answer import setup as answer_setup


def snapshot(memory):
    return {'memory_id':memory['_id'],'revision':memory['revision'],
        'fingerprint':MemoryAnswerService.dependency(memory)['fingerprint'],
        'latest':memory['versions'][-1],'history':memory['versions'][:-1]}


@pytest.fixture
def context(answer_setup,tmp_path,monkeypatch):
    _,reader,extractor,memory=answer_setup
    reader.memories.collection.find.return_value=[memory]
    plan={'dataset_hash':'dataset','memories':[snapshot(memory)],'cases':[
        {'id':key,'question':key,'required_memory_ids':[] if key=='expense-missing' else [memory['_id']],
         'optional_memory_ids':[],'answerability':'unknown','review_checks':[]} for key in trial.CASE_IDS]}
    vector=[1.0]+[0.0]*(DIMENSIONS-1)
    vectors={'document:'+memory['_id']:{'vector':vector}}
    embedder=Mock()
    embedder.query.side_effect=lambda text:EmbeddingResult(tuple(vector),hashlib.sha256(text.encode()).hexdigest(),'RETRIEVAL_QUERY')
    filter_service=Mock()
    def filter_result(question,candidates,**kwargs):
        keep=[] if question=='expense-missing' else [candidates[0]['memory_id']]
        sufficient=question=='exercise'
        return CompactFilterResult(keep_ids=keep,answerability='sufficient' if sufficient else 'insufficient',
                                   limitation=None if sufficient else '尚未有足夠依據')
    filter_service.filter.side_effect=filter_result
    def answer(payload,schema,instruction,**kwargs):
        data=json.loads(payload)
        if not data['memories']:
            return {'passages':[{'text':'沒有相關消費紀錄。','basis':'limitation','sources':[]}],
                    'insufficient_information':True}
        return {'passages':[{'text':'你曾記錄過上陶藝課很開心。','basis':'explicit',
            'sources':[{'memory_id':memory['_id'],'revision':1}]}],'insufficient_information':False}
    extractor.generate.side_effect=answer
    monkeypatch.setattr(retrieval,'PRIVATE_DIR',tmp_path)
    monkeypatch.setattr(trial,'PRIVATE_DIR',tmp_path)
    return plan,vectors,reader,embedder,filter_service,extractor


def test_selected_answer_keeps_original_content_and_scopes_source_reads(answer_setup):
    _,reader,extractor,memory=answer_setup
    reader.memories.collection.find.return_value=[memory]
    service=trial.SelectedAnswerService(reader,extractor,'owner',[snapshot(memory)])
    result=service.generate('owner','question')
    assert result['answer'].passages[0].sources[0].memory_id==memory['_id']
    payload=json.loads(extractor.generate.call_args.args[0])
    assert payload['memories'][0]['latest']['text']==memory['versions'][0]['content']['text']
    assert 'user_uid' not in extractor.generate.call_args.args[0]
    query=reader.memories.collection.find.call_args.args[0]
    assert query['user_uid']=='owner' and query['_id']=={'$in':[memory['_id']]}
    reader.memories.recent.assert_not_called()


def test_selected_answer_rejects_other_uid_or_changed_source(answer_setup):
    _,reader,extractor,memory=answer_setup
    reader.memories.collection.find.return_value=[memory]
    service=trial.SelectedAnswerService(reader,extractor,'owner',[snapshot(memory)])
    with pytest.raises(AnswerError,match='owner_required'):service.collect('other')
    reader.memories.collection.find.assert_not_called()
    changed=deepcopy(memory);changed['revision']=2
    reader.memories.collection.find.return_value=[changed]
    with pytest.raises(AnswerError,match='sources_changed'):service.collect('owner')
    extractor.generate.assert_not_called()


def test_live_three_stage_trial_counts_calls_and_preserves_background(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    verify=Mock();path=tmp_path/'trial.json'
    state=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,verify,path,
                         trial.new_state(plan,'report','model'))
    assert state['state']=='succeeded' and state['call_attempts']==9
    assert embedder.query.call_count==3 and filter_service.filter.call_count==3 and extractor.generate.call_count==3
    embedder.document.assert_not_called()
    mood=state['cases']['mood']['stages']
    assert mood['filter']['decision']['answerability']=='insufficient'
    assert mood['answer']['answer']['passages'][0]['basis']=='explicit'
    assert len(mood['answer']['inputs'])==1
    missing=state['cases']['expense-missing']['stages']['answer']
    assert missing['inputs']==[] and missing['answer']['insufficient_information']
    assert all(item['wall_elapsed_ms'] is not None for item in state['cases'].values())
    assert path.stat().st_mode&0o777==0o600
    reader.memories.collection.insert_one.assert_not_called()
    reader.memories.collection.update_one.assert_not_called()


def test_source_revocation_prevents_any_provider_call(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    with pytest.raises(retrieval.EvaluationError,match='sources_changed'):
        trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,
            Mock(side_effect=retrieval.EvaluationError('sources_changed')),tmp_path/'trial.json',trial.new_state(plan,'h','model'))
    embedder.query.assert_not_called();filter_service.filter.assert_not_called();extractor.generate.assert_not_called()


def test_resume_reuses_completed_query_and_measures_resumed_wall_honestly(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    normal_filter=filter_service.filter.side_effect
    filter_service.filter.side_effect=GeminiError('read_timeout')
    path=tmp_path/'trial.json';state=trial.new_state(plan,'h','model')
    with pytest.raises(GeminiError):
        trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,state)
    assert embedder.query.call_count==1 and filter_service.filter.call_count==1
    assert set(state['cases']['exercise']['stages'])=={'query'}
    resumed=trial.resume_state(path,plan,'h','model',vectors)
    filter_service.filter.side_effect=normal_filter
    finished=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,resumed)
    assert embedder.query.call_count==3 and filter_service.filter.call_count==4
    assert finished['call_attempts']==10
    assert finished['cases']['exercise']['resumed_case'] is True
    assert finished['cases']['exercise']['wall_elapsed_ms'] is None
    assert finished['cases']['mood']['wall_elapsed_ms'] is not None


def test_completed_resume_does_not_repeat_paid_calls(context,tmp_path):
    plan,vectors,reader,embedder,filter_service,extractor=context
    path=tmp_path/'trial.json'
    trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,trial.new_state(plan,'h','model'))
    state=trial.resume_state(path,plan,'h','model',vectors)
    trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,state)
    assert embedder.query.call_count==3 and filter_service.filter.call_count==3 and extractor.generate.call_count==3


@pytest.mark.parametrize('change',['version','query_hash','unknown_keep','answer_reference'])
def test_resume_rejects_incompatible_or_tampered_results(context,tmp_path,change):
    plan,vectors,reader,embedder,filter_service,extractor=context
    path=tmp_path/'trial.json'
    state=trial.evaluate(plan,vectors,reader,'owner',embedder,filter_service,extractor,Mock(),path,trial.new_state(plan,'h','model'))
    stages=state['cases']['exercise']['stages']
    if change=='version':state['answer_prompt']='changed'
    if change=='query_hash':stages['query']['text_hash']='wrong'
    if change=='unknown_keep':stages['filter']['decision']['keep_ids']=['f'*64]
    if change=='answer_reference':stages['answer']['answer']['passages'][0]['sources'][0]['memory_id']='f'*64
    retrieval.checkpoint(path,state)
    with pytest.raises(ValueError):trial.resume_state(path,plan,'h','model',vectors)


def test_preview_never_opens_database_or_initializes_clients(context,monkeypatch,capsys):
    plan,vectors,*_=context
    monkeypatch.setattr(trial,'load_plan',lambda path:plan)
    monkeypatch.setattr(trial,'find_report',lambda plan:'report')
    monkeypatch.setattr(trial,'load_resume',lambda path,plan:{'state':'succeeded','vectors':vectors})
    monkeypatch.setattr(trial,'read_json',lambda path:({},'hash'))
    blocked=Mock(side_effect=AssertionError('no external calls'))
    for name in ('get_settings','DatabaseConnection','GeminiExtractor','GeminiEmbedder'):
        monkeypatch.setattr(trial,name,blocked)
    assert trial.main([])==0
    result=json.loads(capsys.readouterr().out)
    assert result['planned_calls_max']==9 and result['document_embedding_calls']==0
    assert result['provider_calls']==0 and not result['database_accessed']
    blocked.assert_not_called()
