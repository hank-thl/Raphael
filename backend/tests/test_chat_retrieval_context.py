import json
from copy import deepcopy

import pytest

from app.services.memory_answer import AnswerError, MemoryAnswerService
from app.services.retrieval_context import retrieval_context
from test_chat_answer import context
from test_memory_answer import setup as answer_setup


def prior(memory, question='我的旅行怎麼規劃？'):
    return {'user_uid': memory['user_uid'], 'state': 'succeeded', 'question': question,
        'inputs': [MemoryAnswerService.dependency(memory)], 'answer': {'passages': [{
            'text': '當時的記錄內容。', 'basis': 'explicit',
            'sources': [{'memory_id': memory['_id'], 'revision': memory['revision']}]}],
            'insufficient_information': False}}


def test_followup_search_uses_verified_question_context_and_keeps_original_question(context):
    service, reader, extractor, memory, embeddings, search, index = context
    result = service.generate('owner', '那回程呢？', [prior(memory)])
    assert result['retrieval']['mode'] == 'atlas' and result['retrieval']['context_turns'] == 1
    uid, query = search.search.call_args.args
    assert uid == 'owner'
    assert json.loads(query) == {'current_question': '那回程呢？', 'previous_question': '我的旅行怎麼規劃？'}
    payload = json.loads(extractor.generate.call_args.args[0])
    assert payload['question'] == '那回程呢？'
    assert payload['previous_conversation'][0]['question'] == '我的旅行怎麼規劃？'
    assert extractor.generate.call_count == 1


@pytest.mark.parametrize('change', ['other_user', 'failed', 'stale', 'bad_citation', 'unknown_dependency'])
def test_invalid_previous_turn_never_enters_embedding_or_answer(context, change):
    service, reader, extractor, memory, embeddings, search, index = context
    turn = prior(memory)
    if change == 'other_user': turn['user_uid'] = 'other'
    elif change == 'failed': turn['state'] = 'failed'
    elif change == 'stale': turn['inputs'][0]['fingerprint'] = 'old'
    elif change == 'bad_citation': turn['answer']['passages'][0]['sources'][0]['revision'] = 2
    else: turn['inputs'].append({'memory_id': 'unknown', 'fingerprint': None})
    assert service.generate('owner', '問題', [turn])['retrieval']['mode'] == 'atlas'
    search.search.assert_called_once_with('owner', '問題')
    assert json.loads(extractor.generate.call_args.args[0])['previous_conversation'] == []


def test_context_cited_memory_is_retained_even_when_not_in_top_results(context):
    service, reader, extractor, memory, embeddings, search, index = context
    old = deepcopy(memory); old['_id'] = 'b' * 64
    before = [memory, old]
    reader.memories.recent.side_effect = lambda uid: iter(before)
    reader.memories.collection.find.return_value = before
    deps = [MemoryAnswerService.dependency(row) for row in before]
    embeddings.current_many.return_value = {item['memory_id']: item for item in deps}
    search.search.return_value['covered_inputs'] = deps
    result = service.generate('owner', '補充說明？', [prior(old)])
    assert {item['memory_id'] for item in result['inputs']} == {memory['_id'], old['_id']}
    payload = json.loads(extractor.generate.call_args.args[0])
    assert len(payload['memories']) == 2 and len(payload['previous_conversation']) == 1


def test_old_uncited_dependencies_remain_tracked_without_loading_all_memory_content(context):
    service, reader, extractor, memory, embeddings, search, index = context
    uncited = deepcopy(memory); uncited['_id'] = 'b' * 64
    before = [memory, uncited]
    reader.memories.recent.side_effect = lambda uid: iter(before)
    reader.memories.collection.find.return_value = [memory]
    deps = [MemoryAnswerService.dependency(row) for row in before]
    embeddings.current_many.return_value = {item['memory_id']: item for item in deps}
    search.search.return_value['covered_inputs'] = deps
    turn = prior(memory); turn['inputs'] = deps
    result = service.generate('owner', '繼續？', [turn])
    payload = json.loads(extractor.generate.call_args.args[0])
    assert len(payload['memories']) == 1 and len(payload['previous_conversation']) == 1
    assert len(result['inputs']) == 2  # Preserves invalidation when old context inputs change later.


def test_query_context_over_budget_falls_back_before_embedding(context):
    service, reader, extractor, memory, embeddings, search, index = context
    result = service.generate('owner', '那後來呢？', [prior(memory, '字' * 601)])
    assert result['retrieval'] == {'mode': 'direct', 'reason': 'query_context_limit'}
    search.search.assert_not_called()
    assert extractor.generate.call_count == 1


def test_only_three_validated_recent_turns_are_considered(context):
    service, reader, extractor, memory, embeddings, search, index = context
    result = service.generate('owner', '那後來呢？', [prior(memory, f'問題{i}') for i in range(4)])
    payload = json.loads(extractor.generate.call_args.args[0])
    assert result['retrieval']['context_turns'] == 3
    assert [row['question'] for row in payload['previous_conversation']] == ['問題2', '問題1', '問題0']
