from unittest.mock import Mock

import pytest

from app.services.chat_answer import ChatAnswerService
from app.services.embeddings import EmbeddingError
from app.services.memory_answer import AnswerError, MemoryAnswerService
from app.services.memory_search import MemorySearchError
from test_memory_answer import setup as answer_setup


@pytest.fixture
def context(answer_setup, monkeypatch):
    _, reader, extractor, memory = answer_setup
    reader.memories.collection.find.return_value = [memory]
    embeddings, search, collection = Mock(), Mock(), Mock()
    dependency = MemoryAnswerService.dependency(memory)
    embeddings.current_many.return_value = {memory['_id']: dependency}
    search.search.return_value = {'results': [{**dependency, 'revision': 1, 'score': .9}],
        'covered_inputs': [dependency], 'candidate_count': 1, 'candidate_cap_reached': False,
        'validation_version': 'batch-sources-v1'}
    index_check = Mock()
    monkeypatch.setattr('app.services.chat_answer.check_index', index_check)
    service = ChatAnswerService(reader, extractor, embeddings, search, collection,
        owner='owner', enabled=True, fallback_on_missing=True)
    return service, reader, extractor, memory, embeddings, search, index_check


def test_owner_without_context_uses_atlas_and_keeps_validated_sources(context):
    service, reader, extractor, memory, embeddings, search, index = context
    result = service.generate('owner', '問題')
    assert result['retrieval']['mode'] == 'atlas'
    assert result['inputs'] == [MemoryAnswerService.dependency(memory)]
    search.search.assert_called_once_with('owner', '問題')
    assert extractor.generate.call_count == 1 and reader.memories.collection.find.call_count == 2


@pytest.mark.parametrize('reason', ['disabled', 'other_user'])
def test_nonpilot_never_queries_atlas(context, reason):
    service, reader, extractor, memory, embeddings, search, index = context
    uid, previous = 'owner', ()
    if reason == 'disabled':
        service.enabled = False
    elif reason == 'other_user':
        uid = 'other'
        reader.memories.recent.side_effect = lambda uid: iter([])
        extractor.generate.return_value = {'passages': [{'text': '沒有相關紀錄。', 'basis': 'limitation',
            'sources': []}], 'insufficient_information': True}
    assert service.generate(uid, '問題', previous)['retrieval']['mode'] == 'direct'
    search.search.assert_not_called()
    embeddings.current_many.assert_not_called()
    index.assert_not_called()
    assert extractor.generate.call_count == 1


def test_missing_vectors_retain_original_answer_without_embedding_call(context):
    service, reader, extractor, memory, embeddings, search, index = context
    embeddings.current_many.return_value = {}
    result = service.generate('owner', '問題')
    assert result['retrieval'] == {'mode': 'direct', 'reason': 'coverage_unavailable'}
    assert extractor.generate.call_count == 1
    search.search.assert_not_called()


def test_asynchronous_index_missing_one_memory_uses_direct(context):
    service, reader, extractor, memory, embeddings, search, index = context
    search.search.return_value['covered_inputs'] = []
    # Candidate count alone is not evidence of coverage: stale rows can inflate it.
    search.search.return_value['candidate_count'] = 10
    assert service.generate('owner', '問題')['retrieval']['mode'] == 'direct'
    assert extractor.generate.call_count == 1


@pytest.mark.parametrize('error', [EmbeddingError('network_error'), MemorySearchError('index_not_ready')])
def test_search_failure_is_not_retried_or_hidden_by_direct_fallback(context, error):
    service, reader, extractor, memory, embeddings, search, index = context
    search.search.side_effect = error
    with pytest.raises(AnswerError, match='retrieval_unavailable'):
        service.generate('owner', '問題')
    assert search.search.call_count == 1
    extractor.generate.assert_not_called()


def test_scope_source_change_prevents_generation(context):
    service, reader, extractor, memory, embeddings, search, index = context
    reader.memories.collection.find.return_value = []
    with pytest.raises(AnswerError, match='sources_changed'):
        service.generate('owner', '問題')
    extractor.generate.assert_not_called()


def test_entire_coverage_change_after_answer_rejects_result(context):
    service, reader, extractor, memory, embeddings, search, index = context
    original = extractor.generate.return_value
    def generate(*args):
        reader.memories.recent.side_effect = lambda uid: iter([])
        return original
    extractor.generate.side_effect = generate
    with pytest.raises(AnswerError, match='sources_changed'):
        service.generate('owner', '問題')


def test_feature_factory_keeps_original_answerer_when_off(monkeypatch):
    from types import SimpleNamespace
    from app.api import conversations as api
    from app.core.config import Settings
    monkeypatch.setattr(api, 'get_database', lambda request: {'conversations': Mock()})
    service = api.get_conversation_service(SimpleNamespace(), reader=Mock(), settings=Settings())
    assert type(service.answerer) is MemoryAnswerService


def test_feature_factory_creates_owner_gated_wrapper_when_on(monkeypatch):
    from types import SimpleNamespace
    from app.api import conversations as api
    from app.core.config import Settings
    monkeypatch.setattr(api, 'get_database', lambda request: {'conversations': Mock(), 'memory_embeddings': Mock()})
    service = api.get_conversation_service(SimpleNamespace(), reader=Mock(),
        settings=Settings(admin_uid='owner', atlas_chat_enabled=True))
    assert isinstance(service.answerer, ChatAnswerService) and service.answerer.owner == 'owner'


def test_explicit_test_account_uses_its_own_uid_for_search_and_answer(context):
    service, reader, extractor, memory, embeddings, search, index = context
    test_uid = 'Anhy3LoaueQmYvoEeEn1SrntZuo2'
    service.allowed_uids = frozenset({'owner', test_uid})
    memory['user_uid'] = test_uid
    result = service.generate(test_uid, '問題')
    assert result['retrieval']['mode'] == 'atlas'
    search.search.assert_called_once_with(test_uid, '問題')
    assert reader.memories.collection.find.call_args.args[0]['user_uid'] == test_uid
    assert reader.capture.records.find.call_args.args[0]['user_uid'] == test_uid
    assert all(call.args == (test_uid,) for call in reader.memories.recent.call_args_list)


def test_factory_only_adds_explicit_atlas_allowlist(monkeypatch):
    from types import SimpleNamespace
    from app.api import conversations as api
    from app.core.config import Settings
    monkeypatch.setattr(api, 'get_database', lambda request: {'conversations': Mock(), 'memory_embeddings': Mock()})
    settings = Settings(admin_uid='owner', atlas_chat_enabled=True,
        ai_allowed_uids=frozenset({'test', 'another-ai-user'}), atlas_chat_allowed_uids=frozenset({'test'}))
    service = api.get_conversation_service(SimpleNamespace(), reader=Mock(), settings=settings)
    assert service.answerer.allowed_uids == frozenset({'owner', 'test'})


def test_atlas_allowlist_config_parses_without_granting_ai_permission(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv('ATLAS_CHAT_ALLOWED_FIREBASE_UIDS', ' test, test, another, ')
    monkeypatch.setenv('AI_ALLOWED_FIREBASE_UIDS', '')
    monkeypatch.setenv('ADMIN_FIREBASE_UID', 'owner')
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.atlas_chat_allowed_uids == frozenset({'test', 'another'})
        assert settings.ai_user_uids == frozenset({'owner'})
    finally:
        get_settings.cache_clear()
