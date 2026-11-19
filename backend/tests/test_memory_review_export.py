import json
import stat
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from scripts import export_memories_for_review as export


def setup_reader():
    now = datetime.now(timezone.utc)
    row = {'_id': 'a'*64, 'user_uid': 'owner', 'revision': 1, 'created_at': now, 'updated_at': now,
           'versions': [{'revision': 1, 'content': {'text': '考慮旅行', 'topics': ['旅行']}}]}
    reader = Mock()
    reader.memories.recent.return_value.limit.return_value = [row]
    reader.memories.collection.find.return_value = [row]
    reader.get_eligible_many.side_effect = lambda uid, rows: rows
    return reader, row


def test_owner_scope_minimal_snapshot_and_no_writes():
    reader, row = setup_reader()
    result = export.collect(reader, 'owner')
    reader.memories.recent.assert_called_once_with('owner')
    query = reader.memories.collection.find.call_args.args[0]
    assert query['user_uid'] == 'owner' and query['_id'] == {'$in': [row['_id']]}
    assert result['eligible_count'] == 1 and result['provider_calls'] == 0
    assert result['memories'][0]['latest']['content']['text'] == '考慮旅行'
    assert 'user_uid' not in result['memories'][0]
    reader.memories.collection.update_one.assert_not_called()
    reader.memories.collection.insert_one.assert_not_called()


def test_ineligible_sources_are_not_exported():
    reader, _ = setup_reader()
    reader.get_eligible_many.side_effect = [[], []]
    result = export.collect(reader, 'owner')
    assert result['memories'] == [] and result['excluded_count'] == 1


def test_concurrent_changes_abort_export():
    reader, row = setup_reader()
    changed = deepcopy(row); changed['revision'] = 2
    reader.get_eligible_many.side_effect = [[row], [changed]]
    with pytest.raises(ValueError, match='sources_changed'):
        export.collect(reader, 'owner')


def test_limit_and_missing_owner_reject_without_unbounded_scan():
    reader, row = setup_reader()
    with pytest.raises(ValueError, match='owner_required'): export.collect(reader, '')
    reader.memories.recent.assert_not_called()
    reader.memories.recent.return_value.limit.return_value = [row, row]
    with pytest.raises(ValueError, match='memory_limit'): export.collect(reader, 'owner', 1)


def test_private_file_uses_restricted_permissions_and_unique_names(tmp_path, monkeypatch):
    monkeypatch.setattr(export, 'PRIVATE_DIR', tmp_path / 'private')
    path = export.save_private({'memories': []})
    other = export.save_private({'memories': []})
    assert path != other
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert json.loads(path.read_text()) == {'memories': []}


def test_unconfigured_cli_never_opens_database(monkeypatch, capsys):
    from app.core.config import Settings
    monkeypatch.setattr(export, 'get_settings', lambda: Settings())
    connection = Mock()
    monkeypatch.setattr(export, 'DatabaseConnection', connection)
    assert export.main([]) == 1
    connection.assert_not_called()
    assert 'configuration missing' in capsys.readouterr().out
