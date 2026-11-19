import hashlib
import json
from unittest.mock import Mock

import pytest

from app.services.embeddings import DIMENSIONS, EmbeddingResult
from app.services.memory_answer import AnswerError, ANSWER_PROMPT_VERSION, INSTRUCTION
from scripts import try_atlas_answer as cli
from scripts.try_vector_search import INDEX_FILE
from test_memory_embeddings import ctx
from test_memory_answer import setup as answer_setup


@pytest.fixture
def trial_ctx(ctx, answer_setup):
    service, reader, repo, embedder, memory = ctx
    cached, _ = service.save('owner', memory['_id'])
    collection = Mock()
    collection.list_search_indexes.return_value = [{'type': 'vectorSearch', 'status': 'READY',
        'queryable': True, 'latestDefinition': json.loads(INDEX_FILE.read_text())}]
    collection.find_one.return_value = cached
    collection.find.return_value = [cached]
    collection.aggregate.return_value = [{**cached, 'score': .9}]
    embedder.query.side_effect = lambda text: EmbeddingResult(
        (1.,) + (0.,) * (DIMENSIONS - 1), hashlib.sha256(text.encode()).hexdigest(), 'RETRIEVAL_QUERY')
    reader.memories.collection.find.return_value = [memory]
    extractor = Mock()
    extractor.generate.return_value = {'passages': [{'text': '你曾記錄上陶藝課很開心。', 'basis': 'explicit',
        'sources': [{'memory_id': memory['_id'], 'revision': 1}]}], 'insufficient_information': False}
    return reader, collection, embedder, extractor, memory


def test_atlas_to_answer_uses_original_prompt_and_preserves_quotes_and_dependencies(trial_ctx):
    reader, collection, embedder, extractor, memory = trial_ctx
    report = cli.run_trial(reader, collection, 'owner', embedder, extractor, '我記錄過什麼？')
    assert embedder.query.call_count == 1 and extractor.generate.call_count == 1
    payload, schema, prompt = extractor.generate.call_args.args
    data = json.loads(payload)
    assert prompt == INSTRUCTION and report['answer_prompt'] == ANSWER_PROMPT_VERSION
    assert data['memories'][0]['latest']['evidence']['sources'][0]['quote'] == '今天上陶藝課很開心'
    assert report['inputs'][0]['memory_id'] == memory['_id']
    assert report['timings']['query_provider_calls'] == 1
    assert report['timings']['answer_provider_calls'] == 1
    assert report['timings']['selected_collect_calls'] == 2
    assert report['timings']['candidate_batch_validation_calls'] == 1
    assert report['timings']['selected_batch_recheck_calls'] == 1
    assert report['retrieval']['validation_version'] == 'batch-sources-v1'
    assert not report['database_written'] and not report['conversations_written']
    reader.memories.recent.assert_not_called()
    collection.insert_one.assert_not_called()


def test_empty_retrieval_still_allows_a_bounded_unknown_answer(trial_ctx):
    reader, collection, embedder, extractor, memory = trial_ctx
    collection.aggregate.return_value = []
    extractor.generate.return_value = {'passages': [{'text': '沒有洗頭費用的相關紀錄。',
        'basis': 'limitation', 'sources': []}], 'insufficient_information': True}
    report = cli.run_trial(reader, collection, 'owner', embedder, extractor, '洗頭多少錢？')
    assert report['inputs'] == [] and report['answer']['insufficient_information']
    assert json.loads(extractor.generate.call_args.args[0])['memories'] == []


def test_changed_source_between_retrieval_and_answer_prevents_generation(trial_ctx):
    reader, collection, embedder, extractor, memory = trial_ctx
    # Three batched search boundaries succeed; answer's fresh collect sees deletion.
    reader.memories.collection.find.side_effect = [[memory], [memory], [memory], []]
    with pytest.raises(AnswerError, match='sources_changed'):
        cli.run_trial(reader, collection, 'owner', embedder, extractor, '問題')
    extractor.generate.assert_not_called()


def test_source_change_during_generation_rejects_answer(trial_ctx):
    reader, collection, embedder, extractor, memory = trial_ctx
    output = extractor.generate.return_value
    def generate(*args, **kwargs):
        reader.memories.collection.find.return_value = []
        return output
    extractor.generate.side_effect = generate
    with pytest.raises(AnswerError, match='sources_changed'):
        cli.run_trial(reader, collection, 'owner', embedder, extractor, '問題')
    assert extractor.generate.call_count == 1


def test_invalid_model_reference_is_rejected(trial_ctx):
    reader, collection, embedder, extractor, memory = trial_ctx
    extractor.generate.return_value['passages'][0]['sources'][0]['memory_id'] = 'b' * 64
    with pytest.raises(AnswerError, match='invalid_answer'):
        cli.run_trial(reader, collection, 'owner', embedder, extractor, '問題')


def test_preview_never_opens_network(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('no external access'))
    for name in ('get_settings', 'DatabaseConnection', 'GeminiExtractor', 'GeminiEmbedder'):
        monkeypatch.setattr(cli, name, blocked)
    assert cli.main(['--case', 'expense-missing']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['planned_provider_calls_max'] == 2 and report['provider_calls'] == 0
    blocked.assert_not_called()
