from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.extraction import ExtractionInput, MemoryCandidate
from app.services.classification import ClassificationService
from app.services.gemini import GeminiError, generation_schema
from app.services.topics import MemoryTopics
from scripts.try_gemini import example
from test_gemini import extractor, envelope, valid_result
from test_memory_versions import fixture as versions_fixture


def test_v4_schema_requires_topics_and_preserves_multiple_subjects():
    schema = generation_schema()['$defs']['MemoryCandidate']
    assert 'topics' in schema['required']
    result = valid_result()
    result['items'][0]['topics'] = [' 陶藝 ', '生活']
    parsed = extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))
    assert parsed.items[0].topics == ['陶藝', '生活']


@pytest.mark.parametrize('topics', [[], [''], [' '], ['旅行', '旅行'], ['Travel', ' travel '], ['x' * 65], ['x'] * 9, [1], '旅行'])
def test_invalid_new_topics_are_rejected(topics):
    result = valid_result()
    result['items'][0]['topics'] = topics
    with pytest.raises(GeminiError, match='invalid_response'):
        extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))


def test_missing_topics_is_allowed_in_legacy_storage_but_not_new_llm_output():
    result = valid_result()
    result['items'][0].pop('topics')
    assert MemoryCandidate.model_validate(result['items'][0]).topics == []
    with pytest.raises(GeminiError, match='invalid_response'):
        extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))


def test_topic_catalog_reads_only_eligible_owner_latest_versions():
    versions = Mock()
    versions.memories.recent.return_value = [{'_id': 'a'}, {'_id': 'b'}, {'_id': 'deleted'}, {'_id': 'legacy'}]
    documents = {'a': {'versions': [{'content': {'topics': ['old']}}, {'content': {'topics': ['旅行', ' Travel ']}}]},
                 'b': {'versions': [{'content': {'topics': ['travel', '京都']}}]},
                 'legacy': {'versions': [{'content': {}}]}}
    versions.get_eligible.side_effect = lambda uid, identifier: documents.get(identifier)
    assert MemoryTopics(versions).list_names('alice') == sorted(['旅行', 'travel', '京都'])
    versions.memories.recent.assert_called_once_with('alice')
    assert all(call.args[0] == 'alice' for call in versions.get_eligible.call_args_list)
    with pytest.raises(ValueError): MemoryTopics(versions).list_names('')


def test_topic_catalog_limit_fails_instead_of_truncating():
    versions = Mock()
    versions.memories.recent.return_value = [{'_id': str(i)} for i in range(101)]
    versions.get_eligible.side_effect = lambda uid, identifier: {'versions': [{'content': {'topics': [identifier]}}]}
    with pytest.raises(ValueError, match='limit'): MemoryTopics(versions).list_names('alice')


def test_classifier_overrides_caller_catalog_with_authorized_subjects():
    from app.schemas.extraction import ExtractionResult
    model, content_types, topics = Mock(), Mock(), Mock()
    content_types.list_names.return_value = ['journal']
    content_types.resolve.return_value = {'name': 'journal', '_id': 'category'}
    topics.list_names.return_value = ['旅行']
    model.analyze.return_value = ExtractionResult.model_validate(valid_result())
    data = example('multi').model_copy(update={'existing_topics': ['other-user-topic']})
    ClassificationService(model, content_types, topics).analyze('alice', data)
    topics.list_names.assert_called_once_with('alice')
    assert model.analyze.call_args.args[0].existing_topics == ['旅行']
    assert model.analyze.call_args.args[0].content_types == ['journal']


def test_supplement_preserves_old_subjects_without_mutating_history():
    rows, repo, service = versions_fixture()
    rows['a1']['memories'][0]['topics'] = ['生活']
    rows['a2']['memories'][0]['topics'] = ['消費']
    memory = service.create('alice', 'a1', 'c1')
    updated, _ = service.supplement('alice', memory['_id'], 1, 'a2', 'c2')
    assert updated['versions'][0]['content']['topics'] == ['生活']
    assert updated['versions'][1]['content']['topics'] == ['生活', '消費']


def test_topic_input_catalog_is_bounded_and_validated():
    data = example('multi').model_dump(mode='json')
    for values in [['', '旅行'], ['Travel', 'travel'], ['x'] * 101]:
        data['existing_topics'] = values
        with pytest.raises(ValidationError): ExtractionInput.model_validate_json(__import__('json').dumps(data))
