from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
from bson import ObjectId
import pytest
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.schemas.extraction import ExtractionInput, SourceRecord
from app.services.memory_capture import MemoryCaptureService, snapshots
from app.services.memory_versions import MemoryVersionsService


def fixture():
    source = {'_id': ObjectId(), 'user_uid': 'alice', 'deleted_at': None,
              'content': '今天上課很開心', 'created_at': datetime(2026, 11, 14, tzinfo=timezone.utc)}
    data = ExtractionInput(target=SourceRecord(record_id=str(source['_id']), content=source['content'], created_at=source['created_at']), timezone='Asia/Taipei')
    analysis = {'_id': 'analysis', 'user_uid': 'alice', 'target_record_id': str(source['_id']),
                'context_record_ids': [], 'timezone': 'Asia/Taipei', 'source_snapshot': snapshots(data),
                'memories': [{'_id': f'candidate-{i}', 'user_uid': 'alice'} for i in range(20)]}
    docs = [{'_id': f'memory-{i}', 'user_uid': 'alice', 'lifecycle': 'candidate',
             'versions': [{'analysis_id': 'analysis', 'candidate_id': f'candidate-{i}'}]} for i in range(20)]
    records, analyses = Mock(), Mock()
    records.find.side_effect = lambda q: [deepcopy(source)] if source['user_uid'] == q['user_uid'] and source['deleted_at'] is None and source['_id'] in q['_id']['$in'] else []
    analyses.find.side_effect = lambda q: [deepcopy(analysis)] if analysis['user_uid'] == q['user_uid'] and analysis['_id'] in q['_id']['$in'] else []
    reader = MemoryVersionsService(MemoryCaptureService(records, AnalysesRepository(analyses), None, ''), MemoriesRepository(Mock()))
    return reader, docs, source, analysis, records, analyses


def test_twenty_memories_share_two_validation_reads():
    reader, docs, source, analysis, records, analyses = fixture()
    assert reader.get_eligible_many('alice', docs) == docs
    analyses.find.assert_called_once_with({'user_uid': 'alice', '_id': {'$in': ['analysis']}})
    records.find.assert_called_once_with({'user_uid': 'alice', 'deleted_at': None, '_id': {'$in': [source['_id']]}})
    analyses.find_one.assert_not_called(); records.find_one.assert_not_called()


@pytest.mark.parametrize('mutation', ['deleted', 'changed', 'other_owner', 'analysis_owner', 'snapshot_changed'])
def test_batch_preserves_validation(mutation):
    reader, docs, source, analysis, records, analyses = fixture()
    if mutation == 'deleted': source['deleted_at'] = datetime.now(timezone.utc)
    if mutation == 'changed': source['content'] = 'changed'
    if mutation == 'other_owner': source['user_uid'] = 'bob'
    if mutation == 'analysis_owner': analysis['user_uid'] = 'bob'
    if mutation == 'snapshot_changed': analysis['source_snapshot'][0]['content'] = 'wrong'
    assert reader.get_eligible_many('alice', docs) == []


def test_no_cross_request_cache():
    reader, docs, source, analysis, records, analyses = fixture()
    assert len(reader.get_eligible_many('alice', docs)) == 20
    source['deleted_at'] = datetime.now(timezone.utc)
    assert reader.get_eligible_many('alice', docs) == []
    assert records.find.call_count == 2


def test_missing_history_and_foreign_candidate_invalidate_memory():
    reader, docs, source, analysis, records, analyses = fixture()
    docs[0]['versions'].append({'analysis_id': 'missing-old', 'candidate_id': 'old'})
    assert reader.get_eligible_many('alice', docs) == docs[1:]
    analysis['memories'][1]['user_uid'] = 'bob'
    assert reader.get_eligible_many('alice', docs) == docs[2:]


def test_foreign_and_inactive_rows_do_not_trigger_queries():
    reader, docs, source, analysis, records, analyses = fixture()
    assert reader.get_eligible_many('bob', docs) == []
    for doc in docs: doc['lifecycle'] = 'inactive'
    assert reader.get_eligible_many('alice', docs) == []
    records.find.assert_not_called(); analyses.find.assert_not_called()


def test_multiple_sources_validate_in_input_order_and_missing_context_is_rejected():
    reader, docs, source, analysis, records, analyses = fixture()
    context = {**source, '_id': ObjectId(), 'content': '補充內容'}
    analysis['context_record_ids'] = [str(context['_id'])]
    data = ExtractionInput(target=SourceRecord(record_id=str(source['_id']), content=source['content'], created_at=source['created_at']),
        context=[SourceRecord(record_id=str(context['_id']), content=context['content'], created_at=context['created_at'])], timezone='Asia/Taipei')
    analysis['source_snapshot'] = snapshots(data)
    records.find.side_effect = None
    records.find.return_value = [context, source]  # MongoDB need not return $in order.
    assert reader.get_eligible_many('alice', docs) == docs
    records.find.return_value = [source]
    assert reader.get_eligible_many('alice', docs) == []
