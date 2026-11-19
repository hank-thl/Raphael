import pytest

from app.core.config import get_settings
from app.services.memory_answer import AnswerError, MemoryAnswerService
from app.services.retrieved_answer import RetrievedMemoryAnswerService
from test_memory_answer import setup as answer_setup


def test_retrieved_subset_uses_owner_and_fingerprint_and_existing_validation(answer_setup):
    _, reader, extractor, memory = answer_setup
    reader.memories.collection.find.return_value = [memory]
    service = RetrievedMemoryAnswerService(reader, extractor, 'owner', [MemoryAnswerService.dependency(memory)])
    result = service.generate('owner', '問題')
    assert result['inputs'] == [MemoryAnswerService.dependency(memory)]
    assert reader.memories.collection.find.call_count == 2
    reader.memories.recent.assert_not_called()
    with pytest.raises(AnswerError, match='owner_required'):
        service.generate('someone-else', '問題')


def test_changed_fingerprint_prevents_provider_call(answer_setup):
    _, reader, extractor, memory = answer_setup
    reader.memories.collection.find.return_value = [memory]
    service = RetrievedMemoryAnswerService(reader, extractor, 'owner',
        [{'memory_id': memory['_id'], 'fingerprint': 'stale'}])
    with pytest.raises(AnswerError, match='sources_changed'):
        service.generate('owner', '問題')
    extractor.generate.assert_not_called()


def test_feature_flag_defaults_off_and_is_explicit(monkeypatch):
    monkeypatch.delenv('ATLAS_CHAT_ENABLED', raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().atlas_chat_enabled is False
        monkeypatch.setenv('ATLAS_CHAT_ENABLED', 'true')
        get_settings.cache_clear()
        assert get_settings().atlas_chat_enabled is True
    finally:
        get_settings.cache_clear()
