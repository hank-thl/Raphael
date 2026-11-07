from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.repositories.records import RECORD_INDEX, RECORD_INDEX_NAME
from scripts.initialize_records import main


def test_preview_never_opens_database(monkeypatch, capsys):
    monkeypatch.setattr("scripts.initialize_records.get_settings", lambda: Settings())
    connection = Mock()
    monkeypatch.setattr("scripts.initialize_records.DatabaseConnection", connection)
    assert main(["--database", "raphael_dev"]) == 0
    connection.assert_not_called()
    assert "No database request made" in capsys.readouterr().out


def test_wrong_target_cannot_create_index(monkeypatch):
    monkeypatch.setattr("scripts.initialize_records.get_settings", lambda: Settings())
    connection = Mock()
    monkeypatch.setattr("scripts.initialize_records.DatabaseConnection", connection)
    with pytest.raises(SystemExit):
        main(["--database", "wrong", "--apply"])
    connection.assert_not_called()


def test_apply_only_creates_index_and_always_closes_pool(monkeypatch):
    monkeypatch.setattr("scripts.initialize_records.get_settings", lambda: Settings())
    collection = Mock()
    connection = Mock()
    connection.database = {"records": collection}
    monkeypatch.setattr("scripts.initialize_records.DatabaseConnection", Mock(return_value=connection))
    assert main(["--database", "raphael_dev", "--apply"]) == 0
    collection.create_index.assert_called_once_with(RECORD_INDEX, name=RECORD_INDEX_NAME)
    assert [call[0] for call in collection.method_calls] == ["create_index"]
    connection.close.assert_called_once()
