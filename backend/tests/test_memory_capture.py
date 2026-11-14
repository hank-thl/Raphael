from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import pytest

from app.repositories.analyses import AnalysesRepository
from app.schemas.extraction import ExtractionResult
from app.services.memory_capture import CaptureError, MemoryCaptureService


class Records:
    def __init__(self):
        self.rows = {}
        self.queries = []

    def add(self, uid='alice', content='今天上課很開心'):
        identifier = ObjectId()
        self.rows[identifier] = {'_id': identifier, 'user_uid': uid, 'content': content,
                                'created_at': datetime.now(timezone.utc), 'deleted_at': None}
        return str(identifier)

    def find_one(self, query):
        self.queries.append(query)
        row = self.rows.get(query['_id'])
        return deepcopy(row) if row and all(row[k] == v for k, v in query.items()) else None


class Analyses:
    def __init__(self):
        self.rows = {}

    def get(self, uid, identifier):
        row = self.rows.get(identifier)
        return deepcopy(row) if row and row['user_uid'] == uid else None

    def create_once(self, doc):
        return self.rows.setdefault(doc['_id'], deepcopy(doc))


def setup_case():
    records, analyses, classifier = Records(), Analyses(), Mock()
    target = records.add()
    result = ExtractionResult.model_validate({'content_type': 'journal', 'memory_action': 'extract',
        'context_required': False, 'context_record_ids': [], 'items': [{
            'text': '今天上課很開心', 'kind': ['event', 'emotion'], 'status': 'occurred', 'time_text': '今天',
            'evidence': {'type': 'explicit', 'sources': [{'record_id': target, 'quote': '今天上課很開心'}]}}]})
    classifier.analyze.return_value = (result, 'category')
    return records, analyses, classifier, target, MemoryCaptureService(records, analyses, classifier, 'gemini-test')


def test_atomic_document_contains_analysis_memories_provenance_and_reuses_completed_work():
    records, analyses, classifier, target, service = setup_case()
    original = deepcopy(records.rows)
    doc, reused = service.capture('alice', target)
    assert reused is False
    assert doc['processing']['state'] == 'succeeded'
    assert doc['user_uid'] == doc['memories'][0]['user_uid'] == 'alice'
    assert doc['target_record_id'] == target
    assert doc['memories'][0]['lifecycle'] == 'candidate'
    assert doc['memories'][0]['kind'] == ['event', 'emotion']
    assert doc['memories'][0]['evidence']['sources'][0]['record_id'] == target
    assert records.rows == original
    again, reused = service.capture('alice', target)
    assert reused and again['_id'] == doc['_id']
    assert len(analyses.rows) == 1
    classifier.analyze.assert_called_once()
    assert service.get_eligible('bob', doc['_id']) is None


@pytest.mark.parametrize('mutation', ['deleted', 'other_owner', 'missing', 'invalid_id'])
def test_unavailable_sources_never_call_model_or_save(mutation):
    records, analyses, classifier, target, service = setup_case()
    if mutation == 'deleted': records.rows[ObjectId(target)]['deleted_at'] = datetime.now(timezone.utc)
    if mutation == 'other_owner': records.rows[ObjectId(target)]['user_uid'] = 'bob'
    if mutation == 'missing': records.rows.clear()
    if mutation == 'invalid_id': target = 'invalid'
    with pytest.raises(CaptureError, match='source_unavailable'):
        service.capture('alice', target)
    classifier.analyze.assert_not_called()
    assert not analyses.rows


def test_context_ownership_is_enforced_before_model():
    records, analyses, classifier, target, service = setup_case()
    other = records.add('bob')
    with pytest.raises(CaptureError, match='source_unavailable'):
        service.capture('alice', target, [other])
    classifier.analyze.assert_not_called()


@pytest.mark.parametrize('mutation', ['delete', 'change'])
def test_sources_changed_during_model_call_are_not_saved(mutation):
    records, analyses, classifier, target, service = setup_case()
    result = classifier.analyze.return_value
    def analyze(uid, data):
        records.rows[ObjectId(target)]['deleted_at' if mutation == 'delete' else 'content'] = (
            datetime.now(timezone.utc) if mutation == 'delete' else 'changed')
        return result
    classifier.analyze.side_effect = analyze
    with pytest.raises(CaptureError):
        service.capture('alice', target)
    assert not analyses.rows


def test_deleted_or_changed_source_invalidates_stored_analysis():
    records, analyses, classifier, target, service = setup_case()
    doc, _ = service.capture('alice', target)
    assert service.get_eligible('alice', doc['_id']) is not None
    records.rows[ObjectId(target)]['content'] = 'changed'
    assert service.get_eligible('alice', doc['_id']) is None
    records.rows[ObjectId(target)]['content'] = '今天上課很開心'
    records.rows[ObjectId(target)]['deleted_at'] = datetime.now(timezone.utc)
    assert service.get_eligible('alice', doc['_id']) is None


def test_skip_is_successful_without_memories_failure_is_not_saved():
    records, analyses, classifier, target, service = setup_case()
    classifier.analyze.return_value = (ExtractionResult(content_type='noise', memory_action='skip',
        context_required=False, context_record_ids=[], items=[]), 'category')
    doc, _ = service.capture('alice', target)
    assert doc['analysis']['memory_action'] == 'skip' and doc['memories'] == []
    service.model = 'gemini-other'
    classifier.analyze.side_effect = RuntimeError('failure')
    with pytest.raises(RuntimeError): service.capture('alice', target)
    assert len(analyses.rows) == 1


def test_repository_duplicate_writes_return_existing_owner_result():
    collection = Mock()
    collection.insert_one.side_effect = DuplicateKeyError('private-error')
    collection.find_one.return_value = {'_id': 'id', 'user_uid': 'alice'}
    repo = AnalysesRepository(collection)
    assert repo.create_once({'_id': 'id', 'user_uid': 'alice'})['user_uid'] == 'alice'
    collection.find_one.assert_called_with({'_id': 'id', 'user_uid': 'alice'})
    collection.find_one.return_value = None
    with pytest.raises(ValueError): repo.create_once({'_id': 'id', 'user_uid': 'alice'})


def test_trial_defaults_to_dry_run_without_connections(monkeypatch, capsys):
    from scripts import try_memory_capture
    monkeypatch.setattr('sys.argv', ['try_memory_capture'])
    monkeypatch.setattr(try_memory_capture, 'DatabaseConnection', lambda *a: pytest.fail('No DB'))
    assert try_memory_capture.main() == 0
    assert 'Dry run' in capsys.readouterr().out


def test_context_evidence_is_saved_and_context_deletion_invalidates_result():
    records, analyses, classifier, target, service = setup_case()
    context = records.add(content='今天去上課')
    result = classifier.analyze.return_value[0].model_copy(deep=True)
    result.context_required = True
    result.context_record_ids = [context]
    from app.schemas.extraction import EvidenceSource
    result.items[0].evidence.type = 'contextual'
    result.items[0].evidence.sources.append(EvidenceSource(record_id=context, quote='今天去上課'))
    classifier.analyze.return_value = (result, 'category')
    doc, _ = service.capture('alice', target, [context])
    assert len(doc['memories'][0]['evidence']['sources']) == 2
    records.rows[ObjectId(context)]['deleted_at'] = datetime.now(timezone.utc)
    assert service.get_eligible('alice', doc['_id']) is None


def test_invalid_evidence_or_inferred_state_is_not_saved():
    records, analyses, classifier, target, service = setup_case()
    result = classifier.analyze.return_value[0]
    result.items[0].evidence.type = 'inferred'
    with pytest.raises(ValueError): service.capture('alice', target)
    assert not analyses.rows
    result.items[0].evidence.type = 'explicit'
    result.items[0].evidence.sources[0].quote = 'fabricated'
    with pytest.raises(ValueError): service.capture('alice', target)
    assert not analyses.rows


def test_storage_failure_is_not_reported_as_completed():
    records, analyses, classifier, target, service = setup_case()
    analyses.create_once = Mock(side_effect=RuntimeError('write failed'))
    with pytest.raises(RuntimeError): service.capture('alice', target)
    assert not analyses.rows


def test_duplicate_sources_and_missing_owner_fail_before_model():
    records, analyses, classifier, target, service = setup_case()
    with pytest.raises(CaptureError, match='invalid_sources'): service.capture('alice', target, [target])
    with pytest.raises(CaptureError, match='owner_required'): service.capture('', target)
    classifier.analyze.assert_not_called()
