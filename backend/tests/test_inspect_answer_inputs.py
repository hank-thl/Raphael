from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import json

import pytest

from app.core.config import Settings
from app.services.memory_answer import MemoryAnswerService
from scripts import inspect_answer_inputs as cli
from test_memory_answer import setup as answer_setup


@pytest.fixture
def setup(answer_setup, monkeypatch):
    _, reader, model, memory = answer_setup
    now = datetime.now(timezone.utc)
    rid = str(uuid4())
    source = memory['versions'][0]['content']['evidence']['sources'][0]
    records = [{'_id': __import__('bson').ObjectId(source['record_id']), 'content': source['quote'], 'created_at': now}]
    turn = {'user_uid': 'owner', 'request_id': rid, 'question': '我的心理狀態怎麼樣？',
            'updated_at': now, 'inputs': [MemoryAnswerService.dependency(memory)],
            'answer': deepcopy(model.generate.return_value), 'processing': {
                'model': 'gemini-test', 'version': 'memory-answer-v1', 'prompt_version': 'memory-answer-analysis-v1.1'}}
    db = {name: Mock() for name in ['conversations', 'memories', 'records', 'analyses']}
    db['conversations'].find_one.return_value = turn
    db['memories'].find.return_value = [memory]
    db['records'].find.return_value = records
    monkeypatch.setattr(cli, 'MemoryVersionsService', lambda *args: reader)
    return db, turn, memory, reader, model


def test_read_only_inspection_owner_scoped_and_matches_saved_input(setup):
    db, turn, memory, reader, model = setup
    result = cli.inspect(db, 'owner', question=turn['question'])
    assert result['all_memory_inputs_unchanged'] is True
    assert result['input_memory_count'] == result['available_memory_count'] == 1
    assert result['distinct_source_record_count'] == 1
    assert result['memory_inputs'][0]['latest']['text'] == memory['versions'][0]['content']['text']
    assert result['memory_inputs'][0]['cited_revisions'] == [1]
    assert result['processing']['prompt_version'] == 'memory-answer-analysis-v1.1'
    for name in ['conversations', 'memories', 'records']:
        assert db[name].method_calls[0].args[0]['user_uid'] == 'owner'
        assert all(call[0] in {'find', 'find_one'} for call in db[name].method_calls)
    reader.get_eligible_many.assert_called_once_with('owner', [memory])
    model.generate.assert_not_called()
    assert 'user_uid' not in json.dumps(result)


def test_request_id_targets_exact_owner_request(setup):
    db, turn, *_ = setup
    cli.inspect(db, 'owner', question='ignored', request_id=turn['request_id'])
    assert db['conversations'].find_one.call_args.args[0] == {
        'user_uid': 'owner', 'state': 'succeeded', '_id': cli.identifier('owner', turn['request_id'])}


@pytest.mark.parametrize('change', ['content', 'deleted', 'record_disappeared', 'quote_changed'])
def test_changed_inputs_never_display_new_or_deleted_source_text(setup, change):
    db, turn, memory, reader, _ = setup
    if change == 'content': memory['versions'][0]['content']['text'] = 'private changed text'
    if change == 'deleted': reader.get_eligible_many.side_effect = lambda uid, batch: []
    if change == 'record_disappeared': db['records'].find.return_value = []
    if change == 'quote_changed': db['records'].find.return_value[0]['content'] = 'private changed text'
    result = cli.inspect(db, 'owner', question=turn['question'])
    assert result['all_memory_inputs_unchanged'] is False
    assert result['answer'] is None
    assert result['memory_inputs'] == [{'memory_id': memory['_id'], 'state': 'unavailable_or_changed'}]
    assert 'private changed text' not in str(result)


def test_empty_input_answer_remains_inspectable_without_record_query(setup):
    db, turn, *_ = setup
    turn['inputs'] = []
    turn['answer'] = {'passages': [{'text': '沒有足夠資料', 'basis': 'limitation', 'sources': []}],
                      'insufficient_information': True}
    db['memories'].find.return_value = []
    result = cli.inspect(db, 'owner', question=turn['question'])
    assert result['input_memory_count'] == result['distinct_source_record_count'] == 0
    assert result['answer']['insufficient_information'] is True
    db['records'].find.assert_not_called()


def test_missing_answer_is_not_silently_replaced_with_another_question(setup):
    db, turn, *_ = setup
    db['conversations'].find_one.return_value = None
    assert cli.inspect(db, 'owner', question=turn['question']) == {'error': 'answer_not_found'}
    assert db['conversations'].find_one.call_args.args[0]['question'] == turn['question']


def test_missing_owner_fails_before_database_read(setup):
    db, *_ = setup
    with pytest.raises(ValueError, match='owner_required'):
        cli.inspect(db, '', question='test')
    db['conversations'].find_one.assert_not_called()


def test_configuration_missing_never_connects(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings())
    monkeypatch.setattr(cli, 'DatabaseConnection', factory)
    assert cli.main([]) == 1
    factory.assert_not_called()


def test_cli_closes_and_hides_raw_driver_errors(monkeypatch, capsys):
    settings = Settings(admin_uid='owner', mongodb_uri='secret-uri')
    connection = Mock(database={})
    monkeypatch.setattr(cli, 'get_settings', lambda: settings)
    monkeypatch.setattr(cli, 'DatabaseConnection', lambda settings: connection)
    monkeypatch.setattr(cli, 'inspect', Mock(side_effect=RuntimeError('secret-uri')))
    assert cli.main([]) == 1
    connection.close.assert_called_once()
    assert 'secret-uri' not in capsys.readouterr().out
