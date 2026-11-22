from datetime import datetime, timezone
from unittest.mock import Mock

from bson import ObjectId
import pytest

from app.services.memory_capture import digest
from scripts import inspect_memory_routing as cli


def fixture(monkeypatch):
    oid = ObjectId(); rid = str(oid)
    record = {'_id': oid, 'user_uid': 'owner', 'content': '醫生還有開外用a酸',
        'deleted_at': None, 'created_at': datetime.now(timezone.utc),
        'ai_processing': {'state': 'succeeded', 'routing': [{'action': 'uncertain', 'memory_id': 'm', 'revision': 1}]}}
    source = {'record_id': rid, 'content': record['content']}
    analysis = {'_id': 'analysis', 'user_uid': 'owner', 'analysis': {'memory_action': 'extract'},
        'processing': {'prompt_version': 'version'}, 'memories': [{'_id': 'candidate', 'text': '外用a酸'}]}
    route = {'_id': digest(['memory-match-v2', 'owner', 'analysis', 'candidate']), 'user_uid': 'owner',
        'match_version': 'memory-match-v2', 'sources': [source], 'timezone': 'Asia/Taipei',
        'decision': {'action': 'uncertain', 'memory_id': None, 'merged': None}, 'expected_revision': None}
    memory = {'_id': 'm', 'user_uid': 'owner', 'lifecycle': 'candidate', 'revision': 1,
        'versions': [{'revision': 1, 'content': {'text': '外用a酸'}}]}
    db = {name: Mock() for name in ('records', 'analyses', 'memory_routes', 'memories')}
    db['records'].find.return_value.sort.return_value.limit.return_value = [record]
    db['records'].find_one.return_value = record
    db['analyses'].find.return_value.limit.return_value = [analysis]
    db['memory_routes'].find.return_value.limit.return_value = [route]
    capture, versions = Mock(), Mock()
    capture.get_eligible.return_value = analysis
    capture.load.return_value = [source]
    versions.get_eligible.return_value = memory
    monkeypatch.setattr(cli, 'MemoryCaptureService', lambda *args: capture)
    monkeypatch.setattr(cli, 'MemoryVersionsService', lambda *args: versions)
    monkeypatch.setattr(cli, 'snapshots', lambda data: data)
    return db, record, route, capture, versions


def test_reads_saved_decision_and_input_sources_without_inventing_model_reason(monkeypatch):
    db, record, route, capture, versions = fixture(monkeypatch)
    result = cli.collect(db, 'owner', '酸')
    row = result['records'][0]
    assert row['saved_routing'][0]['action'] == 'uncertain'
    inspected = row['route_plans'][0]
    assert inspected['decision']['action'] == 'uncertain'
    assert inspected['candidate'] == {'analysis_id': 'analysis', 'candidate_id': 'candidate'}
    assert inspected['raw_record_input_ids'] == [str(record['_id'])]
    assert inspected['model_reason_saved'] is False and inspected['candidate_options_saved'] is False
    assert result['provider_calls'] == 0 and result['database_written'] is False
    for collection in db.values():
        collection.insert_one.assert_not_called()
        collection.update_one.assert_not_called()
    assert db['records'].find.call_args.args[0]['user_uid'] == 'owner'


def test_term_is_literal_and_query_is_bounded(monkeypatch):
    db, *_ = fixture(monkeypatch)
    cli.collect(db, 'owner', 'a.*酸', 2)
    query = db['records'].find.call_args.args[0]
    assert query['content']['$regex'] == r'a\.\*酸'
    db['records'].find.return_value.sort.return_value.limit.assert_called_once_with(3)


def test_foreign_record_is_rejected(monkeypatch):
    db, record, *_ = fixture(monkeypatch)
    record['user_uid'] = 'other'
    with pytest.raises(ValueError, match='sources_changed'):
        cli.collect(db, 'owner', '酸')


def test_source_change_during_inspection_aborts_export(monkeypatch):
    db, record, route, capture, versions = fixture(monkeypatch)
    capture.load.side_effect = [[route['sources'][0]], []]
    with pytest.raises(ValueError, match='sources_changed'):
        cli.collect(db, 'owner', '酸')


def test_default_preview_never_reads_settings_or_database(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('network forbidden'))
    monkeypatch.setattr(cli, 'get_settings', blocked)
    monkeypatch.setattr(cli, 'DatabaseConnection', blocked)
    assert cli.main(['--contains', '酸']) == 0
    assert '"database_accessed": false' in capsys.readouterr().out
    blocked.assert_not_called()


def test_failed_selection_is_owner_scoped_bounded_and_readonly(monkeypatch):
    db, record, *_ = fixture(monkeypatch)
    record['ai_processing'].update(state='failed', error='matching_context_limit')
    report = cli.collect(db, 'owner', limit=3, failed_only=True)
    assert db['records'].find.call_args.args[0] == {
        'user_uid': 'owner', 'deleted_at': None, 'ai_processing.state': 'failed'}
    db['records'].find.return_value.sort.return_value.limit.assert_called_once_with(4)
    assert report['failed_only'] and report['contains'] is None
    assert report['records'][0]['analysis_error'] == 'matching_context_limit'
    assert report['provider_calls'] == 0 and not report['database_written']
    for collection in db.values():
        collection.update_one.assert_not_called()
        collection.insert_one.assert_not_called()


def test_failed_preview_does_not_connect_or_require_keyword(monkeypatch, capsys):
    blocked = Mock(side_effect=AssertionError('network forbidden'))
    monkeypatch.setattr(cli, 'get_settings', blocked)
    assert cli.main(['--failed']) == 0
    assert '"database_accessed": false' in capsys.readouterr().out
    blocked.assert_not_called()


def test_failed_filter_rejects_successful_or_changed_rows(monkeypatch):
    db, *_ = fixture(monkeypatch)
    with pytest.raises(ValueError, match='sources_changed'):
        cli.collect(db, 'owner', failed_only=True)
