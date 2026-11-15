from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId

from app.core.config import Settings
from scripts import backfill_memory as cli


@pytest.fixture
def setup(monkeypatch):
    records = Mock()
    row = {'_id': ObjectId(), 'content': '測試\n\x1b[31m', 'created_at': datetime.now(timezone.utc)}
    cursor = records.find.return_value
    cursor.sort.return_value.limit.return_value = [row]
    records.update_one.return_value = SimpleNamespace(matched_count=1)
    connection = Mock(database={'records': records})
    factory = Mock(return_value=connection)
    monkeypatch.setattr(cli, 'DatabaseConnection', factory)
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings(admin_uid='owner', mongodb_uri='secret', ai_capture_enabled=True))
    return records, connection, factory, row


def test_preview_is_read_only_and_owner_scoped(setup, capsys):
    records, connection, _, _ = setup
    assert cli.main([]) == 0
    records.update_one.assert_not_called()
    query = records.find.call_args.args[0]
    assert query == {'user_uid': 'owner', 'deleted_at': None, 'ai_processing': {'$exists': False}}
    records.find.return_value.sort.assert_called_once_with([('created_at', 1), ('_id', 1)])
    records.find.return_value.sort.return_value.limit.assert_called_once_with(5)
    connection.close.assert_called_once()
    output = capsys.readouterr().out
    assert '\x1b' not in output and 'secret' not in output


@pytest.mark.parametrize('matched', [0, 1])
def test_apply_rechecks_races_and_only_sets_pending(setup, matched, capsys):
    records, _, _, row = setup
    records.update_one.return_value.matched_count = matched
    assert cli.main(['--apply']) == 0
    query, update = records.update_one.call_args.args
    assert query == {**cli.eligible('owner'), '_id': row['_id']}
    assert set(update['$set']) == {'ai_processing'}
    assert update['$set']['ai_processing']['state'] == 'pending'
    assert f'"queued_count": {matched}' in capsys.readouterr().out


def test_date_filters(setup):
    records, _, _, _ = setup
    assert cli.main(['--since', '2026-11-01T00:00:00+08:00', '--before', '2026-11-02T00:00:00+08:00']) == 0
    bounds = records.find.call_args.args[0]['created_at']
    assert bounds['$gte'] == datetime(2026, 10, 31, 16, tzinfo=timezone.utc)
    assert bounds['$lt'] == datetime(2026, 11, 1, 16, tzinfo=timezone.utc)


@pytest.mark.parametrize('args', [['--limit', '0'], ['--limit', '51'], ['--since', '2026-11-01'],
    ['--since', '2026-11-02T00:00:00Z', '--before', '2026-11-01T00:00:00Z']])
def test_invalid_options_never_open_database(setup, args):
    _, _, factory, _ = setup
    with pytest.raises(SystemExit):
        cli.main(args)
    factory.assert_not_called()


def test_missing_configuration_never_opens_database(setup, monkeypatch):
    _, _, factory, _ = setup
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings())
    assert cli.main([]) == 1
    factory.assert_not_called()


def test_disabled_worker_prevents_apply_but_allows_preview(setup, monkeypatch):
    _, _, factory, _ = setup
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings(admin_uid='owner', mongodb_uri='secret'))
    assert cli.main(['--apply']) == 1
    factory.assert_not_called()
    assert cli.main([]) == 0


def test_partial_failure_reports_known_and_unknown_writes(setup, capsys):
    records, connection, _, row = setup
    records.find.return_value.sort.return_value.limit.return_value = [row, {**row, '_id': ObjectId()}]
    records.update_one.side_effect = [SimpleNamespace(matched_count=1), RuntimeError('secret credentials')]
    assert cli.main(['--apply']) == 1
    output = capsys.readouterr().out
    assert '"confirmed_queued_count": 1' in output
    assert '"write_outcome_may_be_unknown": true' in output
    assert 'secret' not in output
    connection.close.assert_called_once()


def test_read_failure_closes_without_writes(setup, capsys):
    records, connection, _, _ = setup
    records.find.side_effect = RuntimeError('secret credentials')
    assert cli.main([]) == 1
    records.update_one.assert_not_called()
    connection.close.assert_called_once()
    assert 'secret' not in capsys.readouterr().out
