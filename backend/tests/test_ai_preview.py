from dataclasses import replace
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from app.api.ai_preview import get_preview_extractor, _preview_lock
from app.core.config import get_settings
from app.main import app
from app.schemas.extraction import ExtractionResult
from app.services.gemini import GeminiError
from test_records import context as record_context, login_as, write_headers


@pytest.fixture
def preview_context(record_context):
    client, records = record_context
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, admin_uid='user-a')
    model = Mock(model='gemini-test')
    model.analyze.return_value = ExtractionResult(content_type='journal', memory_action='extract',
        context_required=False, context_record_ids=[], items=[{
            'text': '上課開心', 'kind': ['event', 'emotion'], 'topics': ['學習', '心情'],
            'status': 'occurred', 'time_text': None, 'evidence': {'type': 'explicit', 'sources': [
                {'record_id': 'preview_1', 'quote': '上課開心'}]}}])
    app.dependency_overrides[get_preview_extractor] = lambda: model
    return client, records, model


def post(client, **kwargs):
    return client.post('/admin/ai-preview', json={'content': ' 上課開心\n'}, headers=write_headers(client), **kwargs)


def test_owner_preview_preserves_input_and_never_inserts_records(preview_context):
    client, records, model = preview_context
    login_as(client)
    response = post(client)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['persisted'] is False
    assert response.json()['analysis']['items'][0]['topics'] == ['學習', '心情']
    data = model.analyze.call_args.args[0]
    assert data.target.content == ' 上課開心\n' and data.target.record_id == 'preview_1'
    assert data.context == [] and data.content_types == []
    assert records.rows == []
    # This path does not require a memory DB or the background capture flag.
    assert app.state.database_connection.database is None


@pytest.mark.parametrize('identity', [None, 'b'])
def test_non_owner_cannot_invoke_model(preview_context, identity):
    client, _, model = preview_context
    if identity:
        app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda query: None)}
        login_as(client, identity)
    assert post(client).status_code in {401, 403}
    model.analyze.assert_not_called()


def test_csrf_required(preview_context):
    client, _, model = preview_context
    login_as(client)
    assert client.post('/admin/ai-preview', json={'content': 'test'}).status_code == 403
    model.analyze.assert_not_called()


def test_changed_account_rejected(preview_context):
    client, _, model = preview_context
    login_as(client)
    headers = {**write_headers(client), 'X-Raphael-Account': 'user-b'}
    assert client.post('/admin/ai-preview', json={'content': 'test'}, headers=headers).status_code == 409
    model.analyze.assert_not_called()


@pytest.mark.parametrize('body', [{'content': ''}, {'content': ' \n'}, {'content': 'x'*10001},
    {'content': 1}, {'content': 'test', 'user_uid': 'other'}, {'content': 'test', 'context': []}])
def test_invalid_or_extra_fields_rejected_before_model(preview_context, body):
    client, _, model = preview_context
    login_as(client)
    assert client.post('/admin/ai-preview', json=body, headers=write_headers(client)).status_code == 422
    model.analyze.assert_not_called()


@pytest.mark.parametrize('reason,status', [('rate_limited', 429), ('read_timeout', 503), ('not_configured', 503)])
def test_safe_errors_release_lock_and_do_not_retry(preview_context, reason, status):
    client, _, model = preview_context
    login_as(client)
    model.analyze.side_effect = GeminiError(reason)
    response = post(client)
    assert response.status_code == status
    assert model.analyze.call_count == 1 and not _preview_lock.locked()
    assert response.headers['cache-control'] == 'no-store'


def test_concurrent_preview_is_rejected_before_model(preview_context):
    client, _, model = preview_context
    login_as(client)
    _preview_lock.acquire()
    try:
        assert post(client).status_code == 429
        model.analyze.assert_not_called()
    finally:
        _preview_lock.release()


def test_adapter_cannot_bypass_source_validation(preview_context):
    client, _, model = preview_context
    login_as(client)
    model.analyze.return_value.items[0].evidence.sources[0].quote = 'invented'
    assert post(client).status_code == 502
    assert not _preview_lock.locked()


def test_allowed_tester_can_preview_without_admin_or_record_writes(preview_context):
    client, records, model = preview_context
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, ai_allowed_uids=frozenset({'user-b'}))
    app.state.database_connection.database = {'access_users': SimpleNamespace(find_one=lambda query: None)}
    login_as(client, 'b')
    assert post(client).status_code == 200
    assert client.get('/admin/me').status_code == 403
    model.analyze.assert_called_once()
    assert records.rows == []
