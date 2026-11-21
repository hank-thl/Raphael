"""Offline wire/contract checks; these do not grade a live model's topic choices."""
import json

import httpx
import pytest

from app.services.gemini import CONTRACT_VERSION, PROMPT_VERSION, GeminiExtractor
from scripts.try_gemini import TOPIC_CASES, TOPIC_CATALOG, example


@pytest.mark.parametrize('case,topics,kinds,status', [
    ('topics-travel', ['旅行'], ['plan'], 'considering'),
    ('topics-question', ['心理'], ['observation'], None),
    ('topics-feeling', ['心情', '心理'], ['emotion'], None),
    ('topics-activity', ['學習', '心情'], ['event', 'emotion'], 'occurred'),
])
def test_topic_acceptance_inputs_and_multi_aspect_output_roundtrip(case, topics, kinds, status):
    data = example(case)
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        supplied = json.loads(payload['contents'][0]['parts'][0]['text'])
        assert supplied['target']['content'] == TOPIC_CASES[case]
        assert supplied['existing_topics'] == TOPIC_CATALOG
        # Exercise the real adapter, without an external request or database.
        assert 'not an index of' in payload['systemInstruction']['parts'][0]['text']
        calls.append(request)
        result = {'content_type': 'question' if case == 'topics-question' else 'journal',
                  'memory_action': 'extract', 'context_required': False, 'context_record_ids': [],
                  'items': [{'text': data.target.content, 'kind': kinds, 'topics': topics, 'status': status,
                             'time_text': None, 'evidence': {'type': 'explicit', 'sources': [
                                 {'record_id': 'sample_1', 'quote': data.target.content}]}}]}
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': json.dumps(result, ensure_ascii=False)}]}}]})

    result = GeminiExtractor('test', 'gemini-test', transport=httpx.MockTransport(handler)).analyze(data)
    assert result.items[0].topics == topics
    assert result.items[0].kind == kinds
    assert result.items[0].text == TOPIC_CASES[case]
    assert len(calls) == 1


def test_prompt_revision_keeps_existing_contract():
    assert PROMPT_VERSION == 'memory-extraction-topics-v4.2'
    assert CONTRACT_VERSION == 'extraction-preview-v4'
