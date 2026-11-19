import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.embeddings import DIMENSIONS, PROFILE, EmbeddingError, EmbeddingResult
from app.services.memory_answer import MemoryAnswerService
from scripts import evaluate_memory_retrieval as evaluation


def vector(axis=0):
    result = [0.0]*DIMENSIONS
    result[axis] = 1.0
    return result


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'PRIVATE_DIR', tmp_path)
    memories=[]
    for key,text in [('a','document1'),('b','document2')]:
        row={'_id':key*64,'revision':1,'versions':[{'revision':1,'content':{'text':text}}]}
        memories.append({'memory_id':row['_id'],'revision':1,
            'fingerprint':MemoryAnswerService.dependency(row)['fingerprint'],
            'latest':{'revision':1,'content':{'text':text}},'history':[]})
    snapshot={'export_version':'memory-retrieval-review-v1','scope':'configured_admin_only','memories':memories}
    raw=json.dumps(snapshot).encode(); (tmp_path/'source.json').write_bytes(raw)
    ids=[m['memory_id'] for m in memories]
    cases=[{'id':'found','question':'query1','required_memory_ids':ids[:1],
            'optional_memory_ids':[],'irrelevant_memory_ids':ids[1:],
            'answerability':'supported','review_checks':['supported fact']},
           {'id':'missing','question':'query2','required_memory_ids':[],
            'optional_memory_ids':[],'irrelevant_memory_ids':ids,
            'answerability':'absent_from_snapshot','review_checks':['do not invent']}]
    dataset={'dataset_version':'private-retrieval-review-v1','source_export':'source.json',
        'source_sha256':hashlib.sha256(raw).hexdigest(), 'memory_map':memories,'cases':cases}
    path=tmp_path/'cases.json';path.write_text(json.dumps(dataset))
    monkeypatch.setattr(evaluation,'DEFAULT_CASES',path)
    result=evaluation.load_plan(path)
    result['path']=path
    return result


def fake_client():
    def output(text, task):
        return EmbeddingResult(tuple(vector(0 if text in ('document1','query1') else 1)),
                               hashlib.sha256(text.encode()).hexdigest(),task)
    client=Mock()
    client.document.side_effect=lambda text:output(text,'RETRIEVAL_DOCUMENT')
    client.query.side_effect=lambda text:output(text,'RETRIEVAL_QUERY')
    return client


def test_preview_never_loads_secrets_database_or_provider(plan, monkeypatch, capsys):
    no_calls=Mock(side_effect=AssertionError('network not allowed'))
    for name in ['get_settings','DatabaseConnection','GeminiEmbedder']:
        monkeypatch.setattr(evaluation,name,no_calls)
    assert evaluation.main([])==0
    report=json.loads(capsys.readouterr().out)
    assert report['planned_calls']==4 and report['provider_calls']==0
    assert not report['database_accessed'] and not report['database_written']
    no_calls.assert_not_called()


def test_evaluation_ranking_metrics_private_file_and_no_answer_claim(plan,tmp_path):
    client=fake_client(); verify=Mock(); path=tmp_path/'result.json'
    state=evaluation.evaluate(plan,client,verify,path,evaluation.initial_state(plan))
    assert client.document.call_count==2 and client.query.call_count==2
    assert state['state']=='succeeded' and state['call_attempts']==4
    first,missing=state['results']
    assert first['ranking'][0]['memory_id']=='a'*64
    assert first['metrics']['1']=={'required_recall':1.0,'relevant_precision':1.0}
    assert first['reciprocal_rank']==1.0
    assert first['metrics']['3']['relevant_precision']==0.5
    assert missing['metrics']['1']['required_recall'] is None
    assert missing['metrics']['1']['relevant_precision']==0
    assert missing['ranking'] and missing['semantic_passed'] is None
    assert state['summary']['no_positive_case_ids']==['missing']
    assert path.stat().st_mode & 0o777 == 0o600
    assert verify.call_count>=2+2*4


def test_current_owner_scope_and_source_change_rejection(plan):
    reader=Mock()
    rows=[{'_id':m['memory_id'],'user_uid':'owner','revision':1,
           'versions':[m['latest']]} for m in plan['memories']]
    reader.memories.collection.find.return_value=rows
    reader.get_eligible_many.return_value=rows
    evaluation.validate_current(reader,'owner',plan['memories'])
    query=reader.memories.collection.find.call_args.args[0]
    assert query['user_uid']=='owner' and query['lifecycle']=='candidate'
    reader.get_eligible_many.return_value=rows[:1]
    with pytest.raises(evaluation.EvaluationError,match='sources_changed'):
        evaluation.validate_current(reader,'owner',plan['memories'])
    rows[0]['user_uid']='other'
    reader.get_eligible_many.return_value=rows
    with pytest.raises(evaluation.EvaluationError,match='sources_changed'):
        evaluation.validate_current(reader,'owner',plan['memories'])


def test_source_change_before_first_call_sends_nothing(plan,tmp_path):
    client=fake_client()
    verify=Mock(side_effect=evaluation.EvaluationError('sources_changed'))
    with pytest.raises(evaluation.EvaluationError,match='sources_changed'):
        evaluation.evaluate(plan,client,verify,tmp_path/'result.json',evaluation.initial_state(plan))
    client.document.assert_not_called();client.query.assert_not_called()


def test_source_change_after_first_call_prevents_more_requests(plan,tmp_path):
    client=fake_client();verify=Mock(side_effect=[None,None,evaluation.EvaluationError('sources_changed')])
    with pytest.raises(evaluation.EvaluationError,match='sources_changed'):
        evaluation.evaluate(plan,client,verify,tmp_path/'result.json',evaluation.initial_state(plan))
    assert client.document.call_count==1
    client.query.assert_not_called()


def test_resume_keeps_successful_vectors_but_checks_sources(plan,tmp_path):
    path=tmp_path/'result.json';state=evaluation.initial_state(plan)
    first=fake_client()
    first.query.side_effect=EmbeddingError('read_timeout')
    with pytest.raises(EmbeddingError):evaluation.evaluate(plan,first,Mock(),path,state)
    assert len(state['vectors'])==2 and state['call_attempts']==3
    resumed=evaluation.load_resume(path,plan)
    client=fake_client();verify=Mock()
    result=evaluation.evaluate(plan,client,verify,path,resumed)
    client.document.assert_not_called()
    assert client.query.call_count==2 and result['state']=='succeeded'
    assert result['summary']['new_vectors']==2 and result['call_attempts']==5
    verify.assert_called()


def test_completed_resume_makes_no_provider_calls(plan,tmp_path):
    path=tmp_path/'result.json'
    evaluation.evaluate(plan,fake_client(),Mock(),path,evaluation.initial_state(plan))
    client=Mock()
    evaluation.evaluate(plan,client,Mock(),path,evaluation.load_resume(path,plan))
    client.document.assert_not_called();client.query.assert_not_called()


@pytest.mark.parametrize('change',['profile','dataset','vector','hash','unknown'])
def test_resume_tampering_rejected(plan,tmp_path,change):
    state=evaluation.initial_state(plan)
    state['call_attempts']=1
    job=plan['jobs'][0]
    state['vectors'][job['key']]={'kind':job['kind'],'text_hash':job['text_hash'],'vector':vector()}
    if change=='profile':state['profile']='other-model'
    if change=='dataset':state['dataset_hash']='changed'
    if change=='vector':state['vectors'][job['key']]['vector']=[0]*DIMENSIONS
    if change=='hash':state['vectors'][job['key']]['text_hash']='wrong'
    if change=='unknown':state['vectors']['unapproved']={}
    path=tmp_path/'result.json';evaluation.checkpoint(path,state)
    with pytest.raises((evaluation.EvaluationError,EmbeddingError)):evaluation.load_resume(path,plan)


def test_oversized_text_and_modified_snapshot_stop_in_preflight(plan):
    path=plan['path'];dataset=json.loads(path.read_text())
    dataset['cases'][0]['question']='中'*1000
    path.write_text(json.dumps(dataset))
    with pytest.raises(evaluation.EvaluationError,match='input_limit'):evaluation.load_plan(path)
    dataset['source_sha256']='wrong';path.write_text(json.dumps(dataset))
    with pytest.raises(evaluation.EvaluationError,match='snapshot_mismatch'):evaluation.load_plan(path)


def test_invalid_labels_and_duplicate_cases_reject(plan):
    path=plan['path'];dataset=json.loads(path.read_text())
    dataset['cases'][0]['optional_memory_ids']=dataset['cases'][0]['required_memory_ids']
    path.write_text(json.dumps(dataset))
    with pytest.raises(evaluation.EvaluationError,match='invalid_labels'):evaluation.load_plan(path)
    dataset['cases']=[dataset['cases'][0],dataset['cases'][0]];path.write_text(json.dumps(dataset))
    with pytest.raises(evaluation.EvaluationError,match='duplicate_id'):evaluation.load_plan(path)


def test_unknown_exception_never_prints_private_error(plan,monkeypatch,capsys):
    monkeypatch.setattr(evaluation,'get_settings',Mock(side_effect=RuntimeError('secret private-question')))
    assert evaluation.main(['--run'])==1
    out=capsys.readouterr().out
    assert 'secret' not in out and 'private-question' not in out
    assert json.loads(out)['reason']=='evaluation_failed'


def test_paths_must_be_within_private_folder(plan,tmp_path):
    with pytest.raises(evaluation.EvaluationError,match='private_path_required'):
        evaluation.read_json(tmp_path.parent/'outside.json')


def test_invalid_provider_profile_never_used_for_ranking(plan,tmp_path):
    client=fake_client()
    original=client.document.side_effect
    client.document.side_effect=lambda text:replace(original(text),profile='other')
    with pytest.raises(evaluation.EvaluationError,match='embedding_mismatch'):
        evaluation.evaluate(plan,client,Mock(),tmp_path/'result.json',evaluation.initial_state(plan))
    assert client.document.call_count==1
    client.query.assert_not_called()


def test_cli_failure_saves_partial_vectors_without_retry(plan,tmp_path,monkeypatch,capsys):
    from app.core.config import Settings
    monkeypatch.setattr(evaluation,'get_settings',lambda:Settings(
        admin_uid='owner',mongodb_uri='private-uri',gemini_api_key='private-key'))
    db={name:Mock() for name in ('records','analyses','memories')}
    connection=Mock(database=db)
    monkeypatch.setattr(evaluation,'DatabaseConnection',lambda settings:connection)
    monkeypatch.setattr(evaluation,'validate_current',Mock())
    client=fake_client();client.query.side_effect=EmbeddingError('read_timeout')
    monkeypatch.setattr(evaluation,'GeminiEmbedder',lambda key:client)
    assert evaluation.main(['--run'])==1
    output=capsys.readouterr().out;report=json.loads(output)
    assert report['reason']=='read_timeout' and report['automatic_retry'] is False
    assert 'private-key' not in output and 'private-uri' not in output
    state=json.loads(Path(report['report']).read_text())
    assert state['state']=='failed' and state['results']==[]
    assert len(state['vectors'])==2 and client.query.call_count==1
    connection.close.assert_called_once()
