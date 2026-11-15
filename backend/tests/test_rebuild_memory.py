from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId, json_util
import json
import pytest

from app.core.config import Settings
from scripts import rebuild_memory as cli


def matches(row, query):
    for key, wanted in query.items():
        if key == '$or':
            if not any(matches(row, branch) for branch in wanted):
                return False
        elif isinstance(wanted, dict):
            if '$exists' in wanted and (key in row) != wanted['$exists']:
                return False
            if '$in' in wanted and row.get(key) not in wanted['$in']:
                return False
        elif row.get(key) != wanted:
            return False
    return True


class Cursor(list):
    def sort(self, fields):
        for key, direction in reversed(fields):
            super().sort(key=lambda row: row[key], reverse=direction < 0)
        return self

    def limit(self, count):
        return self[:count]


class Collection:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def find(self, query, session=None):
        return Cursor(deepcopy([row for row in self.rows if matches(row, query)]))

    def delete_many(self, query, session=None):
        self.writes.append(('delete', query))
        self.rows[:] = [row for row in self.rows if not matches(row, query)]

    def update_many(self, query, update, session=None):
        self.writes.append(('update', query, update))
        count = 0
        for row in self.rows:
            if matches(row, query):
                row.update(deepcopy(update['$set']))
                count += 1
        return SimpleNamespace(matched_count=count)


@pytest.fixture
def db():
    oid = [ObjectId() for _ in range(5)]
    stamp = datetime.now(timezone.utc)
    records = [{'user_uid': 'owner', '_id': identifier, 'content': f'original-{i}',
                'created_at': stamp, 'deleted_at': None} for i, identifier in enumerate(oid)]
    records[0]['ai_processing'] = {'state': 'succeeded', 'analysis_id': 'old'}
    records[1]['ai_processing'] = {'state': 'failed'}
    records[2]['deleted_at'] = stamp
    records[2]['ai_processing'] = {'state': 'succeeded'}
    records[3]['user_uid'] = 'other'
    # records[4] was a context source without a job. records[1] has no persisted analysis.
    analyses = [{'user_uid': 'owner', '_id': 'analysis', 'target_record_id': str(oid[0]),
                 'context_record_ids': [str(oid[2]), str(oid[4])]},
                {'user_uid': 'owner', '_id': 'summary', 'document_type': 'topic_summary'},
                {'user_uid': 'other', '_id': 'other'}]
    data = {'records': Collection(records), 'analyses': Collection(analyses)}
    for name in cli.COLLECTIONS[1:]:
        data[name] = Collection([{'_id': name, 'user_uid': 'owner'}, {'_id': 'other', 'user_uid': 'other'}])
    return data


def test_selection_includes_analysis_context_and_failed_jobs_but_not_deleted_or_other(db):
    snapshot = cli.collect(db, 'owner', 500)
    assert len(snapshot['records']) == 3
    assert all(row['user_uid'] == 'owner' and row['deleted_at'] is None for row in snapshot['records'])
    assert len(snapshot['derived']['analyses']) == 2
    assert all(not collection.writes for collection in db.values())


def test_never_analyzed_record_is_not_added(db):
    raw = deepcopy(db['records'].rows[4])
    raw['_id'] = ObjectId()
    db['records'].rows.append(raw)
    assert raw['_id'] not in {row['_id'] for row in cli.collect(db, 'owner', 500)['records']}


def test_replace_resets_owner_derived_results_and_job_metadata_only(db):
    original = deepcopy(db['records'].rows)
    snapshot = cli.collect(db, 'owner', 500)
    cli.replace(db, snapshot, session='transaction')
    selected = {row['_id'] for row in snapshot['records']}
    for old, new in zip(original, db['records'].rows):
        assert {k: v for k, v in old.items() if k != 'ai_processing'} == {k: v for k, v in new.items() if k != 'ai_processing'}
        if new['_id'] in selected:
            assert new['ai_processing']['state'] == 'pending'
            assert new['ai_processing']['attempts'] == 0
            assert 'analysis_id' not in new['ai_processing']
        else:
            assert new == old
    for name in cli.COLLECTIONS:
        assert db[name].rows == [{'_id': 'other', 'user_uid': 'other'}]
        assert db[name].writes == [('delete', {'user_uid': 'owner'})]


@pytest.mark.parametrize('state', ['running'])
def test_running_jobs_block_before_any_writes(db, state):
    db['records'].rows[0]['ai_processing']['state'] = state
    with pytest.raises(cli.RebuildError, match='running_jobs_present'):
        cli.collect(db, 'owner', 500)
    assert not any(c.writes for c in db.values())


def test_limits_never_silently_truncate(db, monkeypatch):
    with pytest.raises(cli.RebuildError, match='record_limit'):
        cli.collect(db, 'owner', 1)
    monkeypatch.setattr(cli, 'MAX_DOCUMENTS', 1)
    with pytest.raises(cli.RebuildError, match='derived_document_limit'):
        cli.collect(db, 'owner', 500)


def test_invalid_owner_or_source_fails_closed(db):
    with pytest.raises(cli.RebuildError, match='owner_required'):
        cli.collect(db, '', 500)
    db['analyses'].rows[0]['target_record_id'] = 'invalid'
    with pytest.raises(cli.RebuildError, match='invalid_analysis_source'):
        cli.collect(db, 'owner', 500)


def test_no_active_analyzed_sources_prevents_clear(db):
    db['records'].rows = []
    with pytest.raises(cli.RebuildError, match='no_analyzed_active_records'):
        cli.collect(db, 'owner', 500)


def test_backup_roundtrips_bson_and_has_private_permissions(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'BACKUP_DIR', tmp_path / 'private')
    snapshot = cli.collect(db, 'owner', 500)
    first, second = cli.save_backup(snapshot), cli.save_backup(snapshot)
    assert first != second
    assert first.stat().st_mode & 0o777 == 0o600
    restored = json_util.loads(first.read_text())
    assert restored['records'][0]['_id'] == snapshot['records'][0]['_id']
    assert restored['records'][0]['content'] == snapshot['records'][0]['content']
    assert set(restored['derived']) == set(cli.COLLECTIONS)


class Session:
    def __init__(self, db):
        self.db = db
        self.in_transaction = False
        self.committed = False
        self.aborted = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start_transaction(self, **kwargs):
        assert kwargs['read_concern'].level == 'snapshot'
        assert kwargs['write_concern'].document == {'w': 'majority'}
        self.before = {name: deepcopy(collection.rows) for name, collection in self.db.items()}
        self.in_transaction = True

    def commit_transaction(self):
        self.in_transaction = False
        self.committed = True

    def abort_transaction(self):
        for name, rows in self.before.items():
            self.db[name].rows[:] = rows
        self.in_transaction = False
        self.aborted = True


@pytest.fixture
def command(db, monkeypatch, tmp_path):
    session = Session(db)
    connection = Mock(database=db)
    connection.client.start_session.return_value = session
    factory = Mock(return_value=connection)
    monkeypatch.setattr(cli, 'DatabaseConnection', factory)
    monkeypatch.setattr(cli, 'BACKUP_DIR', tmp_path)
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings(admin_uid='owner', mongodb_uri='secret-uri',
        gemini_api_key='secret-key', gemini_model='gemini-test', ai_capture_enabled=True))
    return connection, session, factory


def test_cli_preview_never_opens_transaction_or_writes(db, command, capsys):
    connection, session, _ = command
    assert cli.main([]) == 0
    assert not session.committed and not any(c.writes for c in db.values())
    connection.client.start_session.assert_not_called()
    assert json.loads(capsys.readouterr().out)['database_written'] is False
    connection.close.assert_called_once()


def test_cli_apply_backs_up_before_deletion_and_commits(db, command, monkeypatch, capsys):
    _, session, _ = command
    save = cli.save_backup
    def backup(snapshot):
        assert not any(c.writes for c in db.values())
        return save(snapshot)
    monkeypatch.setattr(cli, 'save_backup', backup)
    assert cli.main(['--apply']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'requeued' and result['analysis_complete'] is False
    assert result['record_count'] == 3 and session.committed


def test_backup_failure_prevents_any_mutations(db, command, monkeypatch, capsys):
    _, session, _ = command
    monkeypatch.setattr(cli, 'save_backup', Mock(side_effect=OSError('secret-uri')))
    assert cli.main(['--apply']) == 1
    assert not any(c.writes for c in db.values()) and session.aborted
    assert 'secret-uri' not in capsys.readouterr().out


def test_update_failure_aborts_all_owner_deletions(db, command, monkeypatch):
    _, session, _ = command
    before = {name: deepcopy(c.rows) for name, c in db.items()}
    monkeypatch.setattr(db['records'], 'update_many', Mock(return_value=SimpleNamespace(matched_count=0)))
    assert cli.main(['--apply']) == 1
    assert session.aborted
    assert {name: c.rows for name, c in db.items()} == before


def test_commit_unknown_is_reported_without_auto_retry(command, monkeypatch, capsys):
    _, session, _ = command
    commit = Mock(side_effect=RuntimeError('private-error'))
    monkeypatch.setattr(session, 'commit_transaction', commit)
    assert cli.main(['--apply']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['commit_outcome_may_be_unknown'] is True
    assert 'private-error' not in str(result)
    assert commit.call_count == 1


@pytest.mark.parametrize('args', [['--max-records', '0'], ['--max-records', '2001']])
def test_invalid_limits_never_connect(command, args):
    _, _, factory = command
    with pytest.raises(SystemExit):
        cli.main(args)
    factory.assert_not_called()


def test_missing_configuration_never_connects(command, monkeypatch):
    _, _, factory = command
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings())
    assert cli.main(['--apply']) == 1
    factory.assert_not_called()
