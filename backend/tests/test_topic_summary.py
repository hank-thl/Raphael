from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
import json

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.api.topic_summary import get_summary_service
from app.main import app
from app.services.gemini import GeminiError
from app.services.topic_summary import TopicSummaryService, SummaryError
from app.repositories.topic_summaries import TopicSummariesRepository
from test_memory_read_api import document
from test_memory_jobs import enable_owner
from test_records import context as record_context, login_as, write_headers
from test_matching_evaluation import Cursor


class Store:
    def __init__(self): self.rows = {}
    def get(self, uid, identifier):
        row = self.rows.get(identifier)
        return deepcopy(row) if row and row['user_uid'] == uid else None
    def save(self, document):
        self.rows[document['_id']] = deepcopy(document)
        return deepcopy(document)


def fixture():
    rows = [document(1)]
    source_id = str(ObjectId())
    rows[0]['versions'][0]['content'].update(topics=['旅行'], text='正在考慮去京都，尚未決定。', status='considering')
    rows[0]['versions'][0]['content']['evidence']['sources'] = [{'record_id': source_id, 'quote': '想去京都'}]
    invalid = set()
    reader = Mock()
    def find(query):
        matches = [deepcopy(row) for row in rows if row['user_uid'] == query['user_uid'] and row['lifecycle'] == query['lifecycle']]
        if '_id' in query: matches = [row for row in matches if row['_id'] in query['_id']['$in']]
        if 'versions.content.topics' in query: matches = [row for row in matches if any(query['versions.content.topics'] in version['content'].get('topics', []) for version in row['versions'])]
        return Cursor(matches)
    reader.memories.collection.find.side_effect = find
    reader.get_eligible_many.side_effect = lambda uid, documents: [row for row in documents if row['_id'] not in invalid and row['user_uid'] == uid]
    reader.capture.records.find.return_value = [{'_id': ObjectId(source_id), 'created_at': datetime.now(timezone.utc)}]
    model = Mock(model='gemini-test')
    model.generate.return_value = {'title': '京都旅行構想', 'summary': [{'text': '目前考慮前往京都，尚未決定。', 'basis': 'explicit',
        'time_label': None, 'sources': [{'memory_id': rows[0]['_id'], 'revision': 1}]}], 'sections': []}
    repository = Store()
    return TopicSummaryService(reader, repository, model), rows, invalid, model, repository, reader


def test_manual_generation_saves_citations_and_get_does_not_generate():
    service, rows, invalid, model, store, reader = fixture()
    assert service.read('user-a', '旅行')['state'] == 'missing'
    model.generate.assert_not_called()
    result = service.generate('user-a', '旅行')
    assert result['state'] == 'ready' and result['memory_count'] == 1
    assert result['items'][0]['sources'][0]['revision'] == 1
    assert service.read('user-a', '旅行')['state'] == 'ready'
    assert model.generate.call_count == 1
    payload = json.loads(model.generate.call_args.args[0])
    assert payload['memories'][0]['latest']['status'] == 'considering'
    assert 'record_created_at' in payload['memories'][0]['latest']['evidence']['sources'][0]
    assert 'user_uid' not in json.dumps(payload)
    assert next(iter(store.rows.values()))['document_type'] == 'topic_summary'
    assert service.read('other', '旅行')['state'] == 'missing'


def test_added_or_revised_memories_mark_summary_stale_without_auto_generation():
    service, rows, invalid, model, store, reader = fixture()
    service.generate('user-a', '旅行')
    rows[0]['revision'] = 2
    rows[0]['versions'].append(deepcopy(rows[0]['versions'][0]))
    rows[0]['versions'][-1]['revision'] = 2
    rows[0]['versions'][-1]['content']['text'] = '決定下週前往京都'
    assert service.read('user-a', '旅行')['state'] == 'stale'
    rows.append({**deepcopy(rows[0]), '_id': 'b' * 64})
    assert service.read('user-a', '旅行')['state'] == 'stale'
    assert model.generate.call_count == 1


def test_invalid_original_input_hides_all_old_summary_text():
    service, rows, invalid, model, store, reader = fixture()
    rows.append({**deepcopy(rows[0]), '_id': 'b' * 64})  # Uncited input is still a dependency.
    service.generate('user-a', '旅行')
    invalid.add('b' * 64)
    result = service.read('user-a', '旅行')
    assert result['state'] == 'invalid' and result['items'] == []
    assert result['generated_at'] is None


@pytest.mark.parametrize('mutation', ['unknown', 'wrong_revision', 'duplicate', 'inferred_fact'])
def test_invalid_model_citations_are_not_saved(mutation):
    service, rows, invalid, model, store, reader = fixture()
    item = model.generate.return_value['summary'][0]
    if mutation == 'unknown': item['sources'][0]['memory_id'] = 'b'*64
    if mutation == 'wrong_revision': item['sources'][0]['revision'] = 2
    if mutation == 'duplicate': item['sources'] *= 2
    if mutation == 'inferred_fact': rows[0]['versions'][0]['content']['evidence']['type'] = 'inferred'
    with pytest.raises(SummaryError, match='invalid_summary'): service.generate('user-a', '旅行')
    assert not store.rows


def test_sources_changed_while_generating_reject_result():
    service, rows, invalid, model, store, reader = fixture()
    answer = deepcopy(model.generate.return_value)
    def generate(*args):
        invalid.add(rows[0]['_id'])
        return answer
    model.generate.side_effect = generate
    with pytest.raises(SummaryError, match='sources_changed'): service.generate('user-a', '旅行')
    assert not store.rows


def test_provider_failure_does_not_overwrite_saved_summary():
    service, rows, invalid, model, store, reader = fixture()
    service.generate('user-a', '旅行')
    original = deepcopy(store.rows)
    model.generate.side_effect = GeminiError('provider_unavailable')
    with pytest.raises(GeminiError): service.generate('user-a', '旅行')
    assert store.rows == original


def test_no_memories_or_over_limit_never_calls_provider():
    service, rows, invalid, model, store, reader = fixture()
    with pytest.raises(SummaryError, match='no_memories'): service.generate('user-a', '工作')
    rows[:] = [{**deepcopy(rows[0]), '_id': f'{i:064x}'} for i in range(51)]
    with pytest.raises(SummaryError, match='input_limit'): service.generate('user-a', '旅行')
    model.generate.assert_not_called()


def test_payload_limit_is_explicit_without_truncation():
    service, rows, invalid, model, store, reader = fixture()
    rows[0]['versions'][0]['content']['evidence']['sources'][0]['quote'] = 'x' * 10000
    rows[:] = [{**deepcopy(rows[0]), '_id': f'{i:064x}'} for i in range(7)]
    with pytest.raises(SummaryError, match='input_limit'): service.generate('user-a', '旅行')
    model.generate.assert_not_called()


def test_repository_reuses_analyses_with_document_type_and_owner_filter():
    collection = Mock()
    repository = TopicSummariesRepository(collection)
    repository.get('alice', 'id')
    collection.find_one.assert_called_once_with({'_id': 'id', 'user_uid': 'alice', 'document_type': 'topic_summary'})
    repository.save({'_id': 'id', 'user_uid': 'alice', 'document_type': 'topic_summary', 'result': {}})
    query, update = collection.find_one_and_update.call_args.args
    assert query['user_uid'] == 'alice' and query['document_type'] == 'topic_summary'
    assert '_id' not in update['$set']


@pytest.fixture
def api(record_context):
    client, _ = record_context
    enable_owner()
    service = Mock()
    service.read.return_value = {'state': 'missing', 'items': []}
    service.generate.return_value = {'state': 'ready', 'items': []}
    app.dependency_overrides[get_summary_service] = lambda: service
    login_as(client)
    return client, service


def test_get_is_private_and_post_requires_csrf(api):
    client, service = api
    response = client.get('/topic-summary?topic=旅行')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    service.generate.assert_not_called()
    assert client.post('/topic-summary', json={'topic': '旅行'}).status_code == 403
    assert client.post('/topic-summary', json={'topic': '旅行'}, headers=write_headers(client)).status_code == 200
    service.generate.assert_called_once_with('user-a', '旅行')


def test_other_accounts_and_injected_uid_are_rejected(api):
    client, service = api
    assert client.post('/topic-summary', json={'topic': '旅行', 'user_uid': 'user-b'}, headers=write_headers(client)).status_code == 422
    assert client.get('/topic-summary?topic=旅行', headers={'X-Raphael-Account': 'other'}).status_code == 409
    login_as(client, 'b')
    assert client.get('/topic-summary?topic=旅行').status_code == 403
    assert client.post('/topic-summary', json={'topic': '旅行'}, headers=write_headers(client)).status_code == 403
    client.cookies.clear()
    assert client.get('/topic-summary?topic=旅行').status_code == 401
    service.generate.assert_not_called()


@pytest.mark.parametrize('error,status', [(SummaryError('input_limit'),413), (SummaryError('sources_changed'),409),
    (SummaryError('invalid_summary'),502), (GeminiError('provider_unavailable'),503), (OperationFailure('private-password'),503)])
def test_api_errors_are_safe(api, error, status):
    client, service = api
    service.generate.side_effect = error
    response = client.post('/topic-summary', json={'topic': '旅行'}, headers=write_headers(client))
    assert response.status_code == status and 'private-password' not in response.text


def test_inferred_summary_preserves_evidence_label():
    service, rows, invalid, model, store, reader = fixture()
    rows[0]['versions'][0]['content']['evidence']['type'] = 'inferred'
    model.generate.return_value['summary'][0]['basis'] = 'inferred'
    assert service.generate('user-a', '旅行')['items'][0]['basis'] == 'inferred'


def test_topic_cache_is_not_reused_for_other_topic():
    service, rows, invalid, model, store, reader = fixture()
    service.generate('user-a', '旅行')
    assert service.read('user-a', '工作')['state'] == 'missing'
    assert model.generate.call_count == 1


def test_real_summary_wire_contract_with_mock_transport():
    import httpx
    from app.services.gemini import GeminiExtractor
    service, rows, invalid, model, store, reader = fixture()
    answer = deepcopy(model.generate.return_value)
    def handler(request):
        body = json.loads(request.content)
        assert 'untrusted DATA' in body['systemInstruction']['parts'][0]['text']
        schema = body['generationConfig']['responseJsonSchema']
        assert schema['additionalProperties'] is False
        assert {'title', 'summary', 'sections'} <= set(schema['required'])
        assert request.headers['x-goog-api-key'] == 'fake-key'
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': json.dumps(answer)}]}}]})
    service.extractor = GeminiExtractor('fake-key', 'gemini-test', transport=httpx.MockTransport(handler))
    assert service.generate('user-a', '旅行')['state'] == 'ready'


def test_injected_model_system_field_is_rejected():
    service, rows, invalid, model, store, reader = fixture()
    model.generate.return_value['user_uid'] = 'evil'
    with pytest.raises(SummaryError, match='invalid_summary'): service.generate('user-a', '旅行')
    assert not store.rows


def test_get_storage_error_is_safe_and_does_not_generate(api):
    client, service = api
    service.read.side_effect = OperationFailure('private-password')
    response = client.get('/topic-summary?topic=旅行')
    assert response.status_code == 503 and 'private-password' not in response.text
    service.generate.assert_not_called()


def test_legacy_cache_is_preserved_and_marked_for_manual_update():
    service, rows, invalid, model, store, reader = fixture()
    generated = service.generate('user-a', '旅行')
    saved = next(iter(store.rows.values()))
    saved['version'] = 'topic-summary-v1'
    saved['result'] = {'items': generated['items']}
    original = deepcopy(store.rows)
    response = service.read('user-a', '旅行')
    assert response['format'] == 'legacy' and response['state'] == 'stale'
    assert response['content'] is None and response['items']
    assert store.rows == original and model.generate.call_count == 1


def test_single_memory_can_return_short_article_without_sections():
    service, rows, invalid, model, store, reader = fixture()
    response = service.generate('user-a', '旅行')
    assert response['format'] == 'article'
    assert len(response['content']['summary']) == 1 and response['content']['sections'] == []
    assert response['content']['title'] == '京都旅行構想'


def test_complementary_memories_support_one_integrated_paragraph():
    service, rows, invalid, model, store, reader = fixture()
    first = rows[0]['versions'][0]['content']
    first['text'] = '一月想去長崎，但還不知道具體時間。'
    first['evidence']['sources'][0]['quote'] = first['text']
    second = deepcopy(rows[0]); second['_id'] = 'b' * 64
    second['versions'][0]['content']['text'] = '考慮屆時福岡進福岡出，先買去程單程票，回程之後再買。'
    second['versions'][0]['content']['evidence']['sources'][0]['quote'] = second['versions'][0]['content']['text']
    model.generate.return_value['title'] = '長崎旅行規劃'
    rows.append(second)
    passage = model.generate.return_value['summary'][0]
    passage['text'] = '目前考慮一月份前往長崎，具體日期尚未決定。交通傾向福岡進出，先購買去程單程票，回程之後再安排。'
    passage['sources'].append({'memory_id': second['_id'], 'revision': 1})
    response = service.generate('user-a', '旅行')
    assert len(response['content']['summary']) == 1
    assert len(response['content']['summary'][0]['sources']) == 2


def test_payload_contains_history_and_historical_citations_are_versioned():
    service, rows, invalid, model, store, reader = fixture()
    latest = deepcopy(rows[0]['versions'][0]); latest['revision'] = 2
    latest['content'].update(status='confirmed', text='後來決定前往京都')
    rows[0]['versions'].append(latest); rows[0]['revision'] = 2
    passage = model.generate.return_value['summary'][0]
    passage.update(text='原先還在考慮，後來決定前往京都。', basis='contextual')
    passage['sources'].append({'memory_id': rows[0]['_id'], 'revision': 2})
    response = service.generate('user-a', '旅行')
    payload = json.loads(model.generate.call_args.args[0])['memories'][0]
    assert payload['latest']['status'] == 'confirmed'
    assert payload['history'][0]['status'] == 'considering'
    assert [ref['revision'] for ref in response['content']['summary'][0]['sources']] == [1, 2]


@pytest.mark.parametrize('mutation', ['blank_title', 'blank_heading', 'empty_section', 'repeated', 'timeline_without_time', 'legacy_output'])
def test_invalid_article_structure_is_not_saved(mutation):
    service, rows, invalid, model, store, reader = fixture()
    answer = model.generate.return_value
    item = deepcopy(answer['summary'][0])
    if mutation == 'blank_title': answer['title'] = ' '
    if mutation == 'blank_heading': answer['sections'] = [{'heading': ' ', 'layout': 'bullets', 'items': [item]}]
    if mutation == 'empty_section': answer['sections'] = [{'heading': '待確認', 'layout': 'bullets', 'items': []}]
    if mutation == 'repeated': answer['sections'] = [{'heading': '重點', 'layout': 'paragraphs', 'items': [item]}]
    if mutation == 'timeline_without_time':
        item['text'] = '當時仍在考慮旅行'
        answer['sections'] = [{'heading': '變化', 'layout': 'timeline', 'items': [item]}]
    if mutation == 'legacy_output': model.generate.return_value = {'items': [item]}
    with pytest.raises(SummaryError, match='invalid_summary'): service.generate('user-a', '旅行')
    assert not store.rows


def test_optional_sections_support_conflicts_and_timelines_without_rewriting_memories():
    service, rows, invalid, model, store, reader = fixture()
    original = deepcopy(rows)
    answer = model.generate.return_value
    timeline = deepcopy(answer['summary'][0]); timeline.update(text='紀錄當時仍在考慮京都旅行。', time_label='紀錄當時')
    answer['sections'] = [{'heading': '規劃脈絡', 'layout': 'timeline', 'items': [timeline]}]
    response = service.generate('user-a', '旅行')
    assert response['content']['sections'][0]['layout'] == 'timeline'
    assert rows == original
    instruction = model.generate.call_args.args[2]
    assert 'Recency alone' in instruction and 'Absence of a record' in instruction


def test_new_cache_retains_flat_compatibility_but_article_contract_is_clean():
    service, rows, invalid, model, store, reader = fixture()
    response = service.generate('user-a', '旅行')
    stored = next(iter(store.rows.values()))
    assert stored['result']['items'] == response['items']
    assert set(response['content']) == {'title', 'summary', 'sections'}


def test_synthesis_wire_schema_keeps_title_property_while_removing_annotations():
    from app.schemas.memory_synthesis import MemorySynthesisResult
    from app.services.gemini import generation_schema
    schema = generation_schema(MemorySynthesisResult)
    assert schema['properties']['title']['type'] == 'string'
    assert 'title' not in schema
    def validate(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert set(node.get('required', [])) <= node.get('properties', {}).keys()
            for child in node.values(): validate(child)
        elif isinstance(node, list):
            for child in node: validate(child)
    validate(schema)


def test_rewriting_same_revision_hides_summary_instead_of_retargeting_citation():
    service, rows, invalid, model, store, reader = fixture()
    service.generate('user-a', '旅行')
    rows[0]['versions'][0]['content']['text'] = '同版號的內容被重新擷取覆寫'
    response = service.read('user-a', '旅行')
    assert response['state'] == 'invalid' and response['content'] is None and not response['items']
    assert model.generate.call_count == 1


def test_conflicting_memories_are_both_supplied_and_remain_unchanged():
    service, rows, invalid, model, store, reader = fixture()
    rows[0]['versions'][0]['content']['text'] = '旅行已確定使用福岡機場。'
    rows[0]['versions'][0]['content']['evidence']['sources'][0]['quote'] = '旅行已確定使用福岡機場。'
    other = deepcopy(rows[0]); other['_id'] = 'b' * 64
    other['versions'][0]['content']['text'] = '旅行已確定使用東京機場。'
    other['versions'][0]['content']['evidence']['sources'][0]['quote'] = '旅行已確定使用東京機場。'
    rows.append(other)
    original = deepcopy(rows)
    item = model.generate.return_value['summary'][0]
    item['text'] = '紀錄對出發機場有不同說法，目前資訊不足以確認採用哪個安排。'
    item['basis'] = 'contextual'
    item['sources'].append({'memory_id': other['_id'], 'revision': 1})
    result = service.generate('user-a', '旅行')
    payload = json.loads(model.generate.call_args.args[0])
    assert len(payload['memories']) == 2 and len(result['content']['summary'][0]['sources']) == 2
    assert rows == original


def test_insufficient_single_note_needs_no_filler_sections():
    service, rows, invalid, model, store, reader = fixture()
    content = rows[0]['versions'][0]['content']
    content.update(text='開心', topics=['感受'], kind=['emotion'], status=None)
    content['evidence']['sources'][0]['quote'] = '開心'
    model.generate.return_value['title'] = '當下感受'
    model.generate.return_value['summary'][0]['text'] = '這筆紀錄表達了開心的感受；目前資訊不足以確定相關事件或原因。'
    response = service.generate('user-a', '感受')
    assert len(response['content']['summary']) == 1 and response['content']['sections'] == []


def test_inferred_historical_revision_cannot_be_promoted_by_explicit_latest_version():
    service, rows, invalid, model, store, reader = fixture()
    old = rows[0]['versions'][0]
    latest = deepcopy(old); latest['revision'] = 2
    old['content']['evidence']['type'] = 'inferred'
    rows[0]['versions'].append(latest); rows[0]['revision'] = 2
    with pytest.raises(SummaryError, match='invalid_summary'): service.generate('user-a', '旅行')
    assert not store.rows


def test_style_prompt_version_is_saved_without_changing_article_contract_or_auto_generation():
    from app.services.topic_summary import PROMPT_VERSION, SUMMARY_VERSION
    service, rows, invalid, model, store, reader = fixture()
    result = service.generate('user-a', '旅行')
    saved = next(iter(store.rows.values()))
    assert saved['prompt_version'] == PROMPT_VERSION
    assert saved['version'] == SUMMARY_VERSION == 'topic-summary-v2'
    assert result['format'] == 'article'
    saved.pop('prompt_version')  # Existing v2 article remains readable until manual update.
    assert service.read('user-a', '旅行')['state'] == 'ready'
    assert model.generate.call_count == 1
