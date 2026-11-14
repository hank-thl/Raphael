from unittest.mock import Mock

import pytest
from pymongo.errors import DuplicateKeyError

from app.repositories.content_types import ContentTypesRepository, normalize_name
from app.schemas.extraction import ExtractionResult
from app.services.classification import ClassificationService
from scripts.try_gemini import example


class Collection:
    def __init__(self):
        self.rows = {}

    def find(self, query, projection):
        self.query = query
        self.matches = [v for v in self.rows.values() if v['user_uid'] == query['user_uid']]
        return self

    def sort(self, *args):
        return self

    def limit(self, count):
        return self.matches[:count]

    def find_one_and_update(self, query, update, **kwargs):
        assert kwargs['upsert'] is True
        return self.rows.setdefault(query['_id'], {**query, **update['$setOnInsert']})


def test_normalized_identity_owner_isolation_and_catalog_limit():
    collection = Collection()
    repo = ContentTypesRepository(collection)
    first = repo.resolve('alice', ' ＪＯＵＲＮＡＬ ')
    assert repo.resolve('alice', 'journal') == first
    assert repo.resolve('bob', 'journal')['_id'] != first['_id']
    repo.resolve('bob', 'private')
    assert repo.list_names('alice') == ['journal']
    assert collection.query == {'user_uid': 'alice'}
    for i in range(100):
        repo.resolve('alice', str(i))
    with pytest.raises(ValueError, match='limit'):
        repo.list_names('alice')


@pytest.mark.parametrize('name', ['', '  ', 'x' * 65, 'a\x00b'])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        normalize_name(name)


def test_concurrent_insert_recovers_matching_owner_only():
    collection = Mock()
    collection.find_one_and_update.side_effect = DuplicateKeyError('secret')
    collection.find_one.return_value = {'_id': 'id', 'name': 'journal'}
    ContentTypesRepository(collection).resolve('alice', 'journal')
    query = collection.find_one.call_args.args[0]
    assert query['user_uid'] == 'alice' and query['name'] == 'journal'
    collection.find_one.return_value = None
    with pytest.raises(ValueError, match='conflict'):
        ContentTypesRepository(collection).resolve('alice', 'journal')


def skipped(name):
    return ExtractionResult(content_type=name, memory_action='skip', context_required=False,
                            context_record_ids=[], items=[])


def test_catalog_is_supplied_new_name_persisted_and_next_analysis_reuses_it():
    repo = ContentTypesRepository(Collection())
    repo.resolve('alice', 'journal')
    repo.resolve('bob', 'private')
    extractor = Mock()
    extractor.analyze.return_value = skipped('noise')
    service = ClassificationService(extractor, repo)
    result, identifier = service.analyze('alice', example('noise'))
    assert extractor.analyze.call_args.args[0].content_types == ['journal']
    assert result.content_type == 'noise'
    assert identifier == repo.resolve('alice', 'noise')['_id']
    service.analyze('alice', example('noise'))
    assert repo.list_names('alice') == ['journal', 'noise']


def test_failures_do_not_create_categories():
    repo = Mock()
    repo.list_names.return_value = ['journal']
    extractor = Mock()
    extractor.analyze.side_effect = RuntimeError('failure')
    with pytest.raises(RuntimeError):
        ClassificationService(extractor, repo).analyze('alice', example('multi'))
    repo.resolve.assert_not_called()
    extractor.analyze.side_effect = None
    result = skipped('new')
    result.context_record_ids = ['unknown']
    extractor.analyze.return_value = result
    with pytest.raises(ValueError):
        ClassificationService(extractor, repo).analyze('alice', example('multi'))
    repo.resolve.assert_not_called()


def test_prompt_receives_catalog_and_reuse_instruction():
    import json
    import httpx
    from app.services.gemini import GeminiExtractor
    def handler(request):
        payload = json.loads(request.content)
        assert json.loads(payload['contents'][0]['parts'][0]['text'])['content_types'] == ['journal']
        assert 'Return that exact name' in payload['systemInstruction']['parts'][0]['text']
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': skipped('journal').model_dump_json()}]}}]})
    data = example('noise').model_copy(update={'content_types': ['journal']})
    assert GeminiExtractor('fake', 'gemini-test', transport=httpx.MockTransport(handler)).analyze(data).content_type == 'journal'


def test_category_cli_dry_run_never_connects_to_database(monkeypatch, capsys):
    import sys
    from scripts import try_gemini
    monkeypatch.setattr(sys, 'argv', ['try_gemini', '--with-categories', '--dry-run'])
    monkeypatch.setattr(try_gemini, 'analyze_with_categories', lambda *args: pytest.fail('No database access'))
    assert try_gemini.main() == 0
    assert 'no API request or database access' in capsys.readouterr().out


def test_category_database_failure_is_safe_and_closes_connection(monkeypatch):
    from app.core.config import get_settings
    from app.db import DatabaseConnection
    from app.services.gemini import GeminiError
    from scripts.try_gemini import analyze_with_categories
    closed = Mock()
    monkeypatch.setattr(DatabaseConnection, 'close', closed)
    def fail(self):
        raise RuntimeError('secret-mongodb-uri')
    monkeypatch.setattr(DatabaseConnection, 'open', fail)
    with pytest.raises(GeminiError, match='category_storage_failed') as caught:
        analyze_with_categories(get_settings(), Mock(), example('multi'))
    assert 'secret' not in str(caught.value)
    closed.assert_called_once()
