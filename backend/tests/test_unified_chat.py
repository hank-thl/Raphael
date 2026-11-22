from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from bson import ObjectId
import pytest
from pymongo.errors import DuplicateKeyError

from app.api.chat import get_chat_service
from app.core.config import get_settings
from app.main import app
from app.repositories.chat import ChatRepository, decode_cursor, encode_cursor
from app.repositories.conversations import ConversationsRepository
from app.schemas.memory_answer import MemoryAnswer
from app.services.chat import ChatService
from app.services.conversations import ConversationService
from app.services.gemini import GeminiError
from test_records import context as record_context, login_as, write_headers


def matches(row, query):
    for key, expected in query.items():
        value = row.get(key)
        if key == '$or':
            if not any(matches(row, branch) for branch in expected): return False
        elif isinstance(expected, dict):
            if '$in' in expected and value not in expected['$in']: return False
            if '$lt' in expected and not value < expected['$lt']: return False
            if '$gt' in expected and not value > expected['$gt']: return False
        elif value != expected: return False
    return True


class Cursor(list):
    def sort(self, fields):
        for key, direction in reversed(fields): super().sort(key=lambda row: row[key], reverse=direction == -1)
        return self
    def limit(self, limit): return self[:limit]


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


@pytest.fixture
def chat_setup():
    records, turns = Collection(), Collection()
    answerer = Mock()
    answerer.extractor.model = 'test-model'
    answerer.reader.capture.records = records
    answerer.generate.return_value = {'answer': MemoryAnswer(passages=[{
        'text': '一般知識回答', 'basis': 'general', 'sources': []}], insufficient_information=False), 'inputs': []}
    conversations = ConversationService(ConversationsRepository(turns), answerer)
    return ChatService(ChatRepository(records, turns), conversations), records, turns, answerer


def send(service, content, mode='record', uid='owner', request=None):
    return service.send(uid, request or str(uuid4()), content, mode, capture=True, ai=True)


@pytest.mark.parametrize('mode', ['record', 'dialogue'])
def test_one_record_and_one_analysis_job_per_request_even_after_retry(chat_setup, mode):
    service, records, turns, answerer = chat_setup
    request = str(uuid4())
    first = send(service, ' 原始內容\n', mode, request=request)
    again = send(service, ' 原始內容\n', mode, request=request)
    assert first == again and len(records.rows) == 1
    assert records.rows[0]['content'] == ' 原始內容\n'
    assert records.rows[0]['ai_processing']['state'] == 'pending'
    assert len(turns.rows) == (1 if mode == 'dialogue' else 0)
    assert answerer.generate.call_count == (1 if mode == 'dialogue' else 0)
    if mode == 'dialogue':
        assert turns.rows[0]['source_record_id'] == first['record']['id']
    with pytest.raises(ValueError): send(service, '不同文字', mode, request=request)
    with pytest.raises(ValueError): send(service, ' 原始內容\n', 'dialogue' if mode == 'record' else 'record', request=request)
    records.rows[0]['deleted_at'] = datetime.now(timezone.utc)
    with pytest.raises(ValueError): send(service, ' 原始內容\n', mode, request=request)
    assert service.get('owner', request, capture=True, ai=True) is None
    assert service.get('other', request, capture=True, ai=True) is None


def test_dialogue_generation_failure_does_not_undo_original_or_analysis_job(chat_setup):
    service, records, turns, answerer = chat_setup
    answerer.generate.side_effect = GeminiError('network_error')
    request = str(uuid4())
    result = send(service, '我的問題', 'dialogue', request=request)
    assert result['turn']['state'] == 'failed'
    assert len(records.rows) == 1 and records.rows[0]['ai_processing']['state'] == 'pending'
    send(service, '我的問題', 'dialogue', request=request)
    assert answerer.generate.call_count == 1


def test_gap_between_saved_record_and_answer_is_resumable_without_duplicate(chat_setup):
    service, records, turns, answerer = chat_setup
    request = str(uuid4())
    service.repository.save('owner', request, '接著呢', 'dialogue', analyze=True)
    assert service.get('owner', request, capture=True, ai=True)['needs_answer']
    result = send(service, '接著呢', 'dialogue', request=request)
    assert not result['needs_answer'] and result['turn']['state'] == 'succeeded'
    assert len(records.rows) == len(turns.rows) == answerer.generate.call_count == 1


def test_second_submission_while_answer_running_reuses_claim_without_provider_call(chat_setup):
    service, records, turns, answerer = chat_setup
    result = deepcopy(answerer.generate.return_value)
    request = str(uuid4())
    observed = []
    def generate(*args):
        observed.append(send(service, 'same question', 'dialogue', request=request))
        return result
    answerer.generate.side_effect = generate
    final = send(service, 'same question', 'dialogue', request=request)
    assert observed[0]['turn']['state'] == 'running'
    assert final['turn']['state'] == 'succeeded'
    assert len(records.rows) == len(turns.rows) == answerer.generate.call_count == 1


def test_inherited_raw_context_deletion_invalidates_later_answer_and_is_not_reused(chat_setup, monkeypatch):
    service, records, turns, answerer = chat_setup
    from itertools import count
    base, sequence = datetime.now(timezone.utc), count()
    clock = lambda: base + timedelta(seconds=next(sequence))
    monkeypatch.setattr('app.repositories.chat.utc_now', clock)
    monkeypatch.setattr('app.repositories.conversations.utc_now', clock)
    first = send(service, '最早的紀錄')
    send(service, '第一個提問', 'dialogue')
    for text in ['中間紀錄一', '中間紀錄二', '中間紀錄三']:
        send(service, text)
    second = send(service, '第二個提問', 'dialogue')
    old_id = first['record']['id']
    assert old_id in {ref['record_id'] for ref in turns.rows[-1]['record_inputs']}
    assert '最早的紀錄' not in answerer.generate.call_args.args[1]
    assert answerer.generate.call_args.args[2]  # Dependency came through previous dialogue.
    records.rows[0]['deleted_at'] = datetime.now(timezone.utc)
    assert service.get('owner', second['request_id'], capture=True, ai=True)['turn']['state'] == 'invalid'
    send(service, '另一題', 'dialogue')
    assert answerer.generate.call_args.args[2] == []
    assert '最早的紀錄' not in answerer.generate.call_args.args[1]


def test_recent_record_context_reaches_answerer_and_is_invalidated_after_deletion(chat_setup):
    service, records, turns, answerer = chat_setup
    first = send(service, '今天在做陶藝')
    # Ensure chronological order even on a millisecond clock.
    records.rows[0]['created_at'] -= timedelta(seconds=1)
    result = send(service, '剛才我在做什麼？', 'dialogue')
    assert first['record']['id'] in {ref['record_id'] for ref in turns.rows[0]['record_inputs']}
    assert '今天在做陶藝' in answerer.generate.call_args.args[1]
    records.rows[0]['deleted_at'] = datetime.now(timezone.utc)
    row = service.get('owner', result['request_id'], capture=True, ai=True)
    assert row['turn']['state'] == 'invalid' and row['turn']['answer'] is None
    assert service.conversations.records_valid('other', turns.rows[0]) is False
    assert service.conversations.public('other', turns.rows) == []


def test_deleted_dialogue_original_is_hidden_and_cannot_leak_via_legacy_api(chat_setup):
    service, records, turns, answerer = chat_setup
    send(service, 'private original', 'dialogue')
    records.rows[0]['deleted_at'] = datetime.now(timezone.utc)
    assert service.page('owner', 20, None, capture=True, ai=True)['items'] == []
    public = service.conversations.public('owner', turns.rows)[0]
    assert public['state'] == 'invalid' and public['question'] == '' and public['answer'] is None


def test_changed_raw_context_timestamp_also_invalidates_answer(chat_setup):
    service, records, turns, _ = chat_setup
    row = send(service, '問題', 'dialogue')
    records.rows[0]['created_at'] -= timedelta(days=1)
    assert service.get('owner', row['request_id'], capture=True, ai=True)['turn']['state'] == 'invalid'


def test_source_deleted_during_generation_is_not_published(chat_setup):
    service, records, turns, answerer = chat_setup
    result = deepcopy(answerer.generate.return_value)
    def generate(*args):
        records.rows[0]['deleted_at'] = datetime.now(timezone.utc)
        return result
    answerer.generate.side_effect = generate
    with pytest.raises(ValueError, match='source_unavailable'):
        send(service, '問題', 'dialogue')
    assert turns.rows[0]['state'] == 'failed' and turns.rows[0]['answer'] is None


def test_legacy_and_new_history_share_stable_cursor_with_equal_timestamps(chat_setup):
    service, records, turns, answerer = chat_setup
    stamp = datetime(2026, 11, 22, tzinfo=timezone.utc)
    for i in range(5):
        records.insert_one({'_id': ObjectId(f'{i+1:024x}'), 'user_uid': 'owner',
            'content': f'record{i}', 'created_at': stamp, 'deleted_at': None})
        row, _ = service.conversations.repository.start('owner', str(uuid4()), f'legacy{i}')
        turns.rows[-1]['created_at'] = stamp
    send(service, 'new dialogue', 'dialogue')
    send(service, 'other private', 'dialogue', uid='other')
    cursor, seen = None, []
    while True:
        page = service.page('owner', 3, cursor, capture=True, ai=True)
        seen.extend(page['items'])
        if not page['next_cursor']: break
        cursor = decode_cursor(page['next_cursor'])
    assert len(seen) == len({row['id'] for row in seen}) == 11
    assert sum(row['record'] is None for row in seen) == 5
    assert sum(row['record'] is not None and row['turn'] is not None for row in seen) == 1
    assert 'other private' not in str(seen)
    assert len(records.rows) == 7  # No legacy questions copied to records.
    assert len(service.page('owner', 20, None, capture=False, ai=False)['items']) == 6


@pytest.mark.parametrize('value', ['bad', 'W10=', 'bnVsbA=='])
def test_malformed_cursor_rejected(value):
    with pytest.raises(ValueError): decode_cursor(value)


def test_mode_is_captured_in_record_and_old_request_collision_rejected(chat_setup):
    service, records, turns, _ = chat_setup
    request = str(uuid4())
    service.conversations.repository.start('owner', request, 'old question')
    with pytest.raises(ValueError): send(service, 'new', request=request)
    assert not records.rows


def test_api_permissions_csrf_validation_account_and_no_store(record_context, chat_setup):
    client, _ = record_context
    service, records, turns, answerer = chat_setup
    app.dependency_overrides[get_chat_service] = lambda: service
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, admin_uid='user-a', ai_capture_enabled=True)
    app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda query: None)}
    body = {'content': 'question', 'mode': 'dialogue', 'request_id': str(uuid4())}
    assert client.get('/chat').status_code == 401
    login_as(client)
    assert client.post('/chat', json=body).status_code == 403
    response = client.post('/chat', json=body, headers=write_headers(client))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['record']['memory_processing']['state'] == 'pending'
    assert client.get('/chat', headers={'X-Raphael-Account': 'user-b'}).status_code == 409
    assert client.post('/chat', json={**body, 'mode': 'invalid'}, headers=write_headers(client)).status_code == 422
    assert client.post('/chat', json={**body, 'user_uid': 'user-b'}, headers=write_headers(client)).status_code == 422
    login_as(client, 'b')
    assert client.post('/chat', json={**body, 'request_id': str(uuid4())}, headers=write_headers(client)).status_code == 403
    record = client.post('/chat', json={**body, 'mode': 'record', 'request_id': str(uuid4())}, headers=write_headers(client))
    assert record.status_code == 200 and record.json()['record']['memory_processing'] is None
    assert 'ai_processing' not in records.rows[-1]
    assert client.get(f"/chat/{body['request_id']}").status_code == 404
    assert len(client.get('/chat').json()['items']) == 1
