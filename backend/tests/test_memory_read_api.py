from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure
import pytest

from app.api.memories import get_memory_reader, decode_boundary, encode_boundary
from app.main import app
from test_memory_jobs import enable_owner
from test_records import context as record_context, login_as, matches


def document(number):
    return {'_id': f'{number:064x}', 'user_uid': 'user-a', 'lifecycle': 'candidate', 'revision': 1,
        'updated_at': datetime(2026, 11, 14, tzinfo=timezone.utc), 'versions': [{'revision': 1,
        'analysis_id': 'internal', 'candidate_id': 'internal', 'processing': {'model': 'private'},
        'created_at': datetime(2026, 11, 14, tzinfo=timezone.utc), 'content': {'text': 'AI candidate',
        'kind': ['event'], 'status': 'occurred', 'time_text': '今天', 'evidence': {'type': 'explicit',
        'sources': [{'record_id': 'source', 'quote': '原文引用'}]}}}]}


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, fields):
        for key, order in reversed(fields): self.rows.sort(key=lambda row: row[key], reverse=order == -1)
        return self
    def limit(self, count): return self.rows[:count]


@pytest.fixture
def preview(record_context):
    client, _ = record_context
    enable_owner()
    reader = Mock()
    rows = [document(i) for i in range(1, 5)]
    def match(row, query):
        query = dict(query)
        topic = query.pop('versions.content.topics', None)
        return matches(row, query) and (topic is None or any(topic in v['content'].get('topics', []) for v in row['versions']))
    reader.memories.collection.find.side_effect = lambda query: Cursor([deepcopy(row) for row in rows if match(row, query)])
    reader.memories.recent.side_effect = lambda uid: [deepcopy(row) for row in rows if row['user_uid'] == uid and row['lifecycle'] == 'candidate']
    reader.get_eligible.side_effect = lambda uid, identifier: deepcopy(next((row for row in rows if row['_id'] == identifier and row['user_uid'] == uid), None))
    app.dependency_overrides[get_memory_reader] = lambda: reader
    login_as(client)
    return client, reader, rows


def test_list_is_owner_scoped_paginated_and_no_store(preview):
    client, reader, rows = preview
    first = client.get('/memories?limit=2')
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
    assert [row['id'] for row in first.json()['items']] == [document(4)['_id'], document(3)['_id']]
    assert reader.memories.collection.find.call_args.args[0]['user_uid'] == 'user-a'
    second = client.get('/memories', params={'limit': 2, 'cursor': first.json()['next_cursor']})
    assert [row['id'] for row in second.json()['items']] == [document(2)['_id'], document(1)['_id']]
    assert second.json()['next_cursor'] is None
    assert 'processing' not in first.text and 'user_uid' not in first.text


def test_invalid_sources_filtered_but_cursor_advances(preview):
    client, reader, rows = preview
    reader.get_eligible.return_value = None
    reader.get_eligible.side_effect = None
    response = client.get('/memories?limit=2')
    assert response.json()['items'] == [] and response.json()['next_cursor']
    assert decode_boundary(response.json()['next_cursor'])[1] == document(3)['_id']


def test_details_expose_only_readable_version_fields(preview):
    client, reader, rows = preview
    response = client.get('/memories/' + rows[0]['_id'])
    assert response.status_code == 200
    assert response.json()['versions'][0]['evidence']['sources'][0]['quote'] == '原文引用'
    assert not {'analysis_id', 'candidate_id', 'processing'} & response.json()['versions'][0].keys()
    reader.get_eligible.side_effect = None
    reader.get_eligible.return_value = None
    assert client.get('/memories/' + rows[0]['_id']).status_code == 404


@pytest.mark.parametrize('path', ['/memories', '/memories/' + 'a' * 64])
def test_non_owner_and_anonymous_cannot_read(preview, path):
    client, reader, rows = preview
    reader.reset_mock()
    login_as(client, 'b')
    assert client.get(path).status_code == 403
    client.cookies.clear()
    assert client.get(path).status_code == 401
    reader.get_eligible.assert_not_called()
    reader.memories.collection.find.assert_not_called()


@pytest.mark.parametrize('cursor', ['', 'bad', 'e30=', encode_boundary({'updated_at': datetime(2026,1,1), '_id': 'a'*64})])
def test_invalid_cursor_is_rejected(preview, cursor):
    client, reader, rows = preview
    assert client.get('/memories', params={'cursor': cursor}).status_code in {400, 422}


def test_account_guard_and_database_errors_are_safe(preview):
    client, reader, rows = preview
    assert client.get('/memories', headers={'X-Raphael-Account': 'someone-else'}).status_code == 409
    reader.memories.collection.find.side_effect = OperationFailure('mongodb://private-password@host')
    response = client.get('/memories')
    assert response.status_code == 503 and 'private-password' not in response.text


def test_inactive_memory_is_hidden(preview):
    client, reader, rows = preview
    rows[0]['lifecycle'] = 'inactive'
    assert client.get('/memories/' + rows[0]['_id']).status_code == 404


def test_old_memory_exposes_empty_topics_without_backfill(preview):
    client, reader, rows = preview
    assert all(row['topics'] == [] for row in client.get('/memories').json()['items'])
    detail = client.get('/memories/' + rows[0]['_id']).json()
    assert detail['topics'] == [] and detail['versions'][0]['topics'] == []
    assert 'topics' not in rows[0]['versions'][0]['content']


def test_topics_are_returned_in_summary_and_version_without_rewriting_raw_sources(preview):
    client, reader, rows = preview
    rows[0]['versions'][0]['content']['topics'] = ['工作', 'raphael']
    detail = client.get('/memories/' + rows[0]['_id']).json()
    assert detail['topics'] == detail['versions'][0]['topics'] == ['工作', 'raphael']
    assert detail['versions'][0]['evidence']['sources'][0]['quote'] == '原文引用'


def test_topic_counts_include_only_valid_latest_owner_memories(preview):
    client, reader, rows = preview
    rows[0]['versions'][0]['content']['topics'] = ['旅行', '京都']
    rows[1]['versions'][0]['content']['topics'] = ['旅行']
    rows[2]['versions'][0]['content']['topics'] = ['不應顯示']
    existing = reader.get_eligible.side_effect
    reader.get_eligible.side_effect = lambda uid, identifier: None if identifier == rows[2]['_id'] else existing(uid, identifier)
    response = client.get('/memories/topics')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert {row['name']: row['count'] for row in data['topics']} == {'旅行': 2, '京都': 1}
    assert data['unclassified_count'] == 1 and data['total_count'] == 3
    reader.memories.recent.assert_called_once_with('user-a')


def test_topic_filter_uses_latest_version_and_unclassified_keeps_legacy(preview):
    client, reader, rows = preview
    rows[0]['versions'][0]['content']['topics'] = ['旅行', '京都']
    rows[1]['versions'][0]['content']['topics'] = ['旅行']
    # A historical match must not leak into a topic absent from latest content.
    rows[2]['versions'][0]['content']['topics'] = ['旅行']
    newer = deepcopy(rows[2]['versions'][0]); newer['content']['topics'] = ['工作']
    rows[2]['versions'].append(newer)
    data = client.get('/memories', params={'topic': '旅行'}).json()
    assert {row['id'] for row in data['items']} == {rows[0]['_id'], rows[1]['_id']}
    assert reader.memories.collection.find.call_args.args[0]['versions.content.topics'] == '旅行'
    legacy = client.get('/memories', params={'unclassified': 'true'}).json()
    assert [row['id'] for row in legacy['items']] == [rows[3]['_id']]
    assert 'topics' not in rows[3]['versions'][0]['content']


def test_topic_filter_cursor_does_not_repeat_multi_topic_memories(preview):
    client, reader, rows = preview
    for row in rows: row['versions'][0]['content']['topics'] = ['旅行', '京都']
    first = client.get('/memories', params={'topic': '旅行', 'limit': 2}).json()
    second = client.get('/memories', params={'topic': '旅行', 'limit': 2, 'cursor': first['next_cursor']}).json()
    assert len({row['id'] for row in first['items'] + second['items']}) == 4
    assert second['next_cursor'] is None


def test_topic_catalog_checks_identity_and_does_not_leak_database_errors(preview):
    client, reader, rows = preview
    assert client.get('/memories/topics', headers={'X-Raphael-Account': 'other'}).status_code == 409
    reader.memories.recent.side_effect = OperationFailure('private-password')
    response = client.get('/memories/topics')
    assert response.status_code == 503 and 'private-password' not in response.text
    login_as(client, 'b')
    assert client.get('/memories/topics').status_code == 403
    client.cookies.clear()
    assert client.get('/memories/topics').status_code == 401


@pytest.mark.parametrize('query', [{'topic': '旅行', 'unclassified': 'true'}, {'topic': ''}, {'topic': ' '}, {'topic': 'x'*65}])
def test_invalid_topic_filters_fail_without_query(preview, query):
    client, reader, rows = preview
    assert client.get('/memories', params=query).status_code == 422
    reader.memories.collection.find.assert_not_called()
