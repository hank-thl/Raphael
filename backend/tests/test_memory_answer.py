from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
import json

from bson import ObjectId
import pytest

from app.services.memory_answer import AnswerError, MemoryAnswerService


@pytest.fixture
def setup():
    record_id = ObjectId()
    content = {'text': '今天上陶藝課很開心', 'topics': ['學習', '心情'], 'kind': ['event', 'emotion'],
               'status': 'occurred', 'time_text': '今天', 'evidence': {'type': 'explicit', 'sources': [
                   {'record_id': str(record_id), 'quote': '今天上陶藝課很開心'}]}}
    memory = {'_id': 'a'*64, 'user_uid': 'owner', 'revision': 1, 'lifecycle': 'candidate',
              'versions': [{'revision': 1, 'content': content}]}
    reader, extractor = Mock(), Mock()
    reader.memories.recent.side_effect = lambda uid: iter([memory])
    reader.get_eligible_many.side_effect = lambda uid, batch: batch
    reader.capture.records.find.return_value = [{'_id': record_id, 'created_at': datetime.now(timezone.utc)}]
    extractor.generate.return_value = {'passages': [{'text': '你曾提到上陶藝課很開心。', 'basis': 'explicit',
        'sources': [{'memory_id': 'a'*64, 'revision': 1}]}], 'insufficient_information': True}
    return MemoryAnswerService(reader, extractor), reader, extractor, memory


def test_answer_whitelists_input_and_cites_exact_version(setup):
    service, reader, model, _ = setup
    answer = service.answer('owner', '我情緒起伏很大嗎')
    payload = json.loads(model.generate.call_args.args[0])
    assert payload['question'] == '我情緒起伏很大嗎'
    assert 'user_uid' not in model.generate.call_args.args[0]
    assert payload['memories'][0]['latest']['evidence']['sources'][0]['record_created_at']
    assert answer.insufficient_information is True
    assert answer.passages[0].sources[0].revision == 1
    assert reader.memories.recent.call_count == 2
    assert all(call.args == ('owner',) for call in reader.memories.recent.call_args_list)
    assert reader.capture.records.find.call_args.args[0]['user_uid'] == 'owner'
    assert reader.capture.records.find.call_args.args[0]['deleted_at'] is None


@pytest.mark.parametrize('change', ['unknown_id', 'wrong_revision', 'duplicate', 'missing_citation', 'blank', 'extra_uid'])
def test_invalid_answers_rejected(setup, change):
    service, _, model, _ = setup
    result = model.generate.return_value
    passage = result['passages'][0]
    if change == 'unknown_id': passage['sources'][0]['memory_id'] = 'b'*64
    if change == 'wrong_revision': passage['sources'][0]['revision'] = 2
    if change == 'duplicate': passage['sources'] *= 2
    if change == 'missing_citation': passage['sources'] = []
    if change == 'blank': passage['text'] = ' '
    if change == 'extra_uid': result['user_uid'] = 'other'
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', 'question')


def test_inferred_memory_not_promoted_to_explicit(setup):
    service, _, model, memory = setup
    memory['versions'][0]['content']['evidence']['type'] = 'inferred'
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', 'question')
    model.generate.return_value['passages'][0]['basis'] = 'inferred'
    assert service.answer('owner', 'question').passages[0].basis == 'inferred'


@pytest.mark.parametrize('change', ['deleted', 'revision', 'same_revision_edit'])
def test_sources_changed_during_model_call_discards_answer(setup, change):
    service, reader, _, memory = setup
    changed = deepcopy(memory)
    if change == 'revision': changed['revision'] = 2
    if change == 'same_revision_edit': changed['versions'][0]['content']['text'] = 'changed'
    reader.get_eligible_many.side_effect = [[memory], [] if change == 'deleted' else [changed]]
    with pytest.raises(AnswerError, match='sources_changed'):
        service.answer('owner', 'question')


def test_missing_raw_source_prevents_model_call(setup):
    service, reader, model, _ = setup
    reader.capture.records.find.return_value = []
    with pytest.raises(AnswerError, match='sources_changed'):
        service.answer('owner', 'question')
    model.generate.assert_not_called()


def test_concurrent_new_memory_does_not_invalidate_unchanged_answer_inputs(setup):
    service, reader, model, memory = setup
    extra = deepcopy(memory); extra['_id'] = 'b' * 64
    reader.get_eligible_many.side_effect = [[memory], [memory, extra]]
    result = service.generate('owner', 'question')
    assert len(result['inputs']) == 1 and result['inputs'][0]['memory_id'] == memory['_id']


def test_no_memories_can_only_return_insufficient_answer(setup):
    service, reader, model, _ = setup
    reader.memories.recent.side_effect = lambda uid: iter([])
    model.generate.return_value = {'passages': [{'text': '目前沒有足夠的記憶可以判斷。', 'basis': 'limitation', 'sources': []}],
                                   'insufficient_information': True}
    assert service.answer('owner', 'question').insufficient_information is True
    model.generate.return_value['insufficient_information'] = False
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', 'question')


def test_too_many_memories_fails_before_model_call(setup):
    service, reader, model, memory = setup
    reader.memories.recent.side_effect = lambda uid: iter([memory]*51)
    with pytest.raises(AnswerError, match='input_limit'):
        service.answer('owner', 'question')
    model.generate.assert_not_called()


def test_oversize_payload_not_truncated(setup):
    service, _, model, memory = setup
    memory['versions'][0]['content']['text'] = 'x'*60001
    with pytest.raises(AnswerError, match='input_limit'):
        service.answer('owner', 'question')
    model.generate.assert_not_called()


@pytest.mark.parametrize('question', ['', ' \n', 'x'*10001])
def test_invalid_question_never_calls_model(setup, question):
    service, _, model, _ = setup
    with pytest.raises(AnswerError, match='invalid_question'):
        service.answer('owner', question)
    model.generate.assert_not_called()


def test_many_invalid_sources_also_have_a_scan_bound(setup):
    service, reader, model, memory = setup
    reader.memories.recent.side_effect = lambda uid: iter([memory]*501)
    reader.get_eligible_many.side_effect = lambda uid, batch: []
    with pytest.raises(AnswerError, match='input_limit'):
        service.answer('owner', 'question')
    model.generate.assert_not_called()


def test_analysis_can_draw_tentative_inference_from_explicit_memories_without_changing_them(setup):
    service, _, model, memory = setup
    before = deepcopy(memory)
    source = {'memory_id': memory['_id'], 'revision': 1}
    model.generate.return_value = {'passages': [
        {'text': '你記錄了上陶藝課時感到開心。', 'basis': 'explicit', 'sources': [source]},
        {'text': '從這一則看，這次活動可能帶來正向感受，但不代表每次上課都如此。',
         'basis': 'inferred', 'sources': [source]},
        {'text': '缺少其他時間點的情緒強度與持續時間，還無法比較起伏幅度。',
         'basis': 'limitation', 'sources': []},
    ], 'insufficient_information': True}
    answer = service.answer('owner', '分析一下最近的心情')
    assert [part.basis for part in answer.passages] == ['explicit', 'inferred', 'limitation']
    assert answer.insufficient_information is True
    assert memory == before
    assert answer.passages[1].sources[0].memory_id == memory['_id']


def test_general_knowledge_without_personal_memories_is_answerable(setup):
    service, reader, model, _ = setup
    reader.memories.recent.side_effect = lambda uid: iter([])
    model.generate.return_value = {'passages': [{'text': '這是一般知識的解釋。', 'basis': 'general', 'sources': []}],
                                   'insufficient_information': False}
    assert service.answer('owner', '什麼是習慣？').passages[0].basis == 'general'
    assert model.generate.call_count == 1


def test_general_knowledge_cannot_claim_memory_citations(setup):
    service, _, model, _ = setup
    model.generate.return_value['passages'][0]['basis'] = 'general'
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', '什麼是習慣？')


def test_general_paragraph_does_not_exempt_personal_claim_from_citations(setup):
    service, _, model, _ = setup
    model.generate.return_value['passages'].insert(0, {'text': '一般知識。', 'basis': 'general', 'sources': []})
    model.generate.return_value['passages'][1]['sources'] = []
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', '這與我的紀錄有何關係？')


def test_mixed_general_and_personal_answer_keeps_distinct_evidence(setup):
    service, _, model, memory = setup
    model.generate.return_value['passages'].insert(0, {'text': '一般知識。', 'basis': 'general', 'sources': []})
    answer = service.answer('owner', '這與我的紀錄有何關係？')
    assert answer.passages[0].sources == []
    assert answer.passages[1].sources[0].memory_id == memory['_id']


def test_uncited_personal_lack_of_information_still_requires_flag(setup):
    service, reader, model, _ = setup
    reader.memories.recent.side_effect = lambda uid: iter([])
    model.generate.return_value = {'passages': [{'text': '缺少這件事的紀錄。', 'basis': 'limitation', 'sources': []}],
                                   'insufficient_information': False}
    with pytest.raises(AnswerError, match='invalid_answer'):
        service.answer('owner', '我上星期見了誰？')
