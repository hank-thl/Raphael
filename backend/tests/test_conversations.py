from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError

from app.api.conversations import get_conversation_service
from app.core.config import get_settings
from app.main import app
from app.repositories.conversations import ConversationsRepository, decode_cursor, encode_cursor, identifier
from app.repositories.records import utc_now
from app.schemas.memory_answer import MemoryAnswer
from app.services.conversations import ConversationService
from app.services.gemini import GeminiError
from app.services.memory_answer import MemoryAnswerService
from test_memory_answer import setup as answer_setup
from test_records import context as record_context, login_as, write_headers


def matches(row, query):
    for key, value in query.items():
        if key == '$or':
            if not any(matches(row, branch) for branch in value): return False
        elif isinstance(value, dict):
            if '$gt' in value and not row[key] > value['$gt']: return False
            if '$lt' in value and not row[key] < value['$lt']: return False
            if '$in' in value and row[key] not in value['$in']: return False
        elif row.get(key) != value: return False
    return True


class Cursor(list):
    def sort(self, fields):
        for key, direction in reversed(fields): super().sort(key=lambda row: row[key], reverse=direction < 0)
        return self
    def limit(self, value): return self[:value]


class Collection:
    def __init__(self): self.rows = []
    def insert_one(self, row):
        if any(old['_id'] == row['_id'] for old in self.rows): raise DuplicateKeyError('duplicate')
        self.rows.append(deepcopy(row))
    def find_one(self, query): return next((deepcopy(row) for row in self.rows if matches(row, query)), None)
    def find(self, query): return Cursor(deepcopy([row for row in self.rows if matches(row, query)]))
    def find_one_and_update(self, query, update, **kwargs):
        for row in self.rows:
            if matches(row, query):
                row.update(deepcopy(update['$set'])); return deepcopy(row)
        return None


def test_repository_idempotent_owner_scope_and_conflicts():
    repo = ConversationsRepository(Collection())
    rid = str(uuid4())
    first, created = repo.start('alice', rid, 'question')
    second, reused = repo.start('alice', rid, 'question')
    assert created and not reused and first == second
    with pytest.raises(ValueError, match='request_conflict'): repo.start('alice', rid, 'different')
    assert repo.get('bob', rid) is None
    other, _ = repo.start('bob', rid, 'other')
    assert other['_id'] != first['_id']
    assert all(row['user_uid'] == 'alice' for row in repo.page('alice'))


def test_repository_only_active_lease_can_finish():
    collection = Collection(); repo = ConversationsRepository(collection)
    row, _ = repo.start('alice', str(uuid4()), 'question')
    collection.rows[0]['lease_until'] = utc_now() - timedelta(seconds=1)
    assert repo.finish(row, 'succeeded', answer={}) is None
    assert collection.rows[0]['state'] == 'running'


def test_pagination_cursor_and_owner_isolation():
    repo = ConversationsRepository(Collection())
    for i in range(4): repo.start('alice', str(uuid4()), f'q{i}')
    repo.start('bob', str(uuid4()), 'private')
    page = repo.page('alice', 2)
    assert len(page) == 3
    older = repo.page('alice', 2, decode_cursor(encode_cursor(page[1])))
    assert len(older) == 2
    assert not {r['_id'] for r in page[:2]} & {r['_id'] for r in older}
    assert all(row['question'] != 'private' for row in page + older)


@pytest.mark.parametrize('cursor', ['bad', 'W10=', 'bnVsbA=='])
def test_invalid_cursor_rejected(cursor):
    with pytest.raises(ValueError): decode_cursor(cursor)


@pytest.fixture
def conversation_setup():
    collection = Collection()
    repo = ConversationsRepository(collection)
    answerer = Mock()
    answerer.extractor.model = 'gemini-test'
    answerer.generate.return_value = {'answer': MemoryAnswer(passages=[{
        'text': '目前資訊不足以判斷。', 'basis': 'limitation', 'sources': []}], insufficient_information=True), 'inputs': []}
    return ConversationService(repo, answerer), collection, answerer


def test_turn_saved_separately_and_repeated_request_never_calls_model_twice(conversation_setup):
    service, collection, answerer = conversation_setup
    rid = str(uuid4())
    result = service.ask('alice', rid, 'question')
    assert result['state'] == 'succeeded' and result['answer']['insufficient_information']
    assert 'user_uid' not in result and 'inputs' not in result
    assert service.ask('alice', rid, 'question') == result
    assert len(collection.rows) == 1 and answerer.generate.call_count == 1
    assert collection.rows[0]['user_uid'] == 'alice'
    assert set(collection.rows[0]) >= {'question', 'answer', 'processing'}


def test_running_request_does_not_invoke_provider_again(conversation_setup):
    service, _, answerer = conversation_setup
    rid = str(uuid4()); service.repository.start('alice', rid, 'question')
    assert service.ask('alice', rid, 'question')['state'] == 'running'
    answerer.generate.assert_not_called()


def test_expired_request_is_publicly_failed_without_get_write(conversation_setup):
    service, collection, answerer = conversation_setup
    row, _ = service.repository.start('alice', str(uuid4()), 'question')
    collection.rows[0]['lease_until'] = utc_now() - timedelta(seconds=1)
    before = deepcopy(collection.rows)
    assert service.public('alice', collection.rows)[0]['error'] == 'interrupted'
    assert collection.rows == before
    answerer.generate.assert_not_called()


@pytest.mark.parametrize('error,expected', [(GeminiError('rate_limited'), 'rate_limited'), (GeminiError('read_timeout'), 'generation_failed')])
def test_failed_answer_saves_question_and_safe_failure(conversation_setup, error, expected):
    service, collection, answerer = conversation_setup
    answerer.generate.side_effect = error
    result = service.ask('alice', str(uuid4()), 'question')
    assert result['state'] == 'failed' and result['error'] == expected
    assert collection.rows[0]['question'] == 'question' and collection.rows[0]['answer'] is None


def test_history_source_changed_hides_saved_answer_and_previous_context(conversation_setup):
    service, collection, answerer = conversation_setup
    answerer.reader.memories.collection.find.return_value = []
    row, _ = service.repository.start('alice', str(uuid4()), 'old')
    row = service.repository.finish(row, 'succeeded', answer={
        'passages': [{'text': 'private old answer', 'basis': 'explicit', 'sources': [{'memory_id': 'a'*64, 'revision': 1}]}],
        'insufficient_information': False}, inputs=[{'memory_id': 'a'*64, 'fingerprint': 'old'}])
    result = service.public('alice', [row])[0]
    assert result['state'] == 'invalid' and result['answer'] is None
    assert 'private old answer' not in str(result)
    assert answerer.reader.memories.collection.find.call_args.args[0]['user_uid'] == 'alice'
    assert service.public('bob', [row]) == []


def test_valid_previous_turn_context_is_whitelisted_and_invalid_context_excluded(answer_setup):
    service, _, model, memory = answer_setup
    previous = {'state': 'succeeded', 'question': '剛才的提問', 'user_uid': 'private-owner',
                'inputs': [service.dependency(memory)], 'answer': model.generate.return_value}
    generated = service.generate('owner', '接續問題', [previous])
    payload = __import__('json').loads(model.generate.call_args.args[0])
    assert payload['previous_conversation'][0]['question'] == '剛才的提問'
    assert 'private-owner' not in model.generate.call_args.args[0]
    assert generated['inputs'] == [service.dependency(memory)]
    previous['inputs'][0]['fingerprint'] = 'changed'
    service.generate('owner', '接續問題', [previous])
    assert __import__('json').loads(model.generate.call_args.args[0])['previous_conversation'] == []


@pytest.fixture
def api(record_context):
    client, records = record_context
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, admin_uid='user-a')
    service = Mock()
    service.repository.page.return_value = []
    service.public.return_value = []
    service.ask.return_value = {'state': 'succeeded'}
    app.dependency_overrides[get_conversation_service] = lambda: service
    return client, records, service


def test_api_owner_csrf_no_record_writes_and_get_no_generation(api):
    client, records, service = api
    login_as(client)
    rid = str(uuid4())
    assert client.get('/conversations').status_code == 200
    service.ask.assert_not_called()
    response = client.post('/conversations', json={'content': ' question\n', 'request_id': rid}, headers=write_headers(client))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    service.ask.assert_called_once_with('user-a', rid, ' question\n')
    assert records.rows == []


@pytest.mark.parametrize('who', [None, 'b'])
def test_non_owner_api_rejected(api, who):
    client, _, service = api
    if who:
        app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda query: None)}
        login_as(client, who)
    assert client.get('/conversations').status_code in {401, 403}
    assert client.post('/conversations', json={'content': 'q', 'request_id': str(uuid4())}, headers=write_headers(client)).status_code in {401, 403}
    service.ask.assert_not_called(); service.repository.page.assert_not_called()


def test_post_csrf_and_account_change(api):
    client, _, service = api; login_as(client)
    body = {'content': 'q', 'request_id': str(uuid4())}
    assert client.post('/conversations', json=body).status_code == 403
    assert client.post('/conversations', json=body, headers={**write_headers(client), 'X-Raphael-Account': 'other'}).status_code == 409
    service.ask.assert_not_called()


@pytest.mark.parametrize('extra', [{'user_uid': 'other'}, {'save_as_memory': True}])
def test_system_fields_not_accepted(api, extra):
    client, _, service = api; login_as(client)
    assert client.post('/conversations', json={'content': 'q', 'request_id': str(uuid4()), **extra}, headers=write_headers(client)).status_code == 422
    service.ask.assert_not_called()


def test_api_invalid_inputs_and_cursor(api):
    client, _, service = api; login_as(client)
    for content, rid in [(' ', str(uuid4())), ('x'*10001, str(uuid4())), ('q', 'not-a-uuid')]:
        assert client.post('/conversations', json={'content': content, 'request_id': rid}, headers=write_headers(client)).status_code == 422
    assert client.get('/conversations', params={'cursor': 'bad'}).status_code == 400
    service.ask.assert_not_called()


def test_missing_request_status_is_404_without_generation(api):
    client, _, service = api; login_as(client)
    service.repository.get.return_value = None
    assert client.get(f'/conversations/{uuid4()}').status_code == 404
    service.ask.assert_not_called()


def test_saved_answer_read_requires_exact_memory_fingerprint(conversation_setup, answer_setup):
    service, _, _ = conversation_setup
    answerer, reader, model, memory = answer_setup
    service.answerer = answerer
    reader.memories.collection.find.return_value = [memory]
    row, _ = service.repository.start('owner', str(uuid4()), 'question')
    row = service.repository.finish(row, 'succeeded', answer=model.generate.return_value,
                                    inputs=[answerer.dependency(memory)])
    result = service.public('owner', [row])[0]
    assert result['state'] == 'succeeded' and result['answer'] is not None
    memory['versions'][0]['content']['text'] = 'same ID and revision, different text'
    assert service.public('owner', [row])[0]['state'] == 'invalid'
    assert service.public('owner', [row])[0]['answer'] is None


def test_single_status_query_scoped_to_session_uid_and_has_no_model_call(api):
    client, _, service = api
    login_as(client)
    rid = str(uuid4())
    service.repository.get.return_value = {'_id': 'owner-turn'}
    service.public.return_value = [{'state': 'running'}]
    assert client.get(f'/conversations/{rid}').status_code == 200
    service.repository.get.assert_called_once_with('user-a', rid)
    service.ask.assert_not_called()


def test_answer_prompt_version_is_saved_without_regenerating_old_history(conversation_setup):
    from app.services.memory_answer import ANSWER_PROMPT_VERSION, ANSWER_VERSION
    service, collection, answerer = conversation_setup
    rid = str(uuid4())
    service.ask('alice', rid, 'question')
    assert collection.rows[0]['processing']['version'] == ANSWER_VERSION
    assert collection.rows[0]['processing']['prompt_version'] == ANSWER_PROMPT_VERSION
    # Old rows remain readable; a prompt revision does not rewrite dialogue history.
    collection.rows[0]['processing'].pop('prompt_version')
    result = service.ask('alice', rid, 'question')
    assert result['state'] == 'succeeded' and answerer.generate.call_count == 1


def test_v2_general_answer_is_persisted_and_readable_without_memories(conversation_setup):
    service, _, answerer = conversation_setup
    answerer.generate.return_value = {'answer': MemoryAnswer(passages=[{
        'text': '一般知識解釋。', 'basis': 'general', 'sources': []}], insufficient_information=False), 'inputs': []}
    rid = str(uuid4())
    result = service.ask('alice', rid, '什麼是習慣？')
    assert result['answer']['passages'][0]['basis'] == 'general'
    assert result['answer']['insufficient_information'] is False
    assert service.ask('alice', rid, '什麼是習慣？') == result
    assert answerer.generate.call_count == 1


def test_legacy_v1_history_is_still_readable(conversation_setup):
    service, collection, answerer = conversation_setup
    rid = str(uuid4())
    service.ask('alice', rid, 'question')
    collection.rows[0]['processing'] = {'version': 'memory-answer-v1', 'prompt_version': 'memory-answer-analysis-v1.3'}
    assert service.ask('alice', rid, 'question')['state'] == 'succeeded'
    assert answerer.generate.call_count == 1
