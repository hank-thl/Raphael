from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db import DatabaseConnection, get_database
from app.main import app


@pytest.mark.parametrize("settings", [Settings(), Settings(mongodb_uri="", mongodb_database="db"),
    Settings(mongodb_uri="mongodb://unused", mongodb_database="")])
def test_missing_configuration_does_not_initialize_driver(monkeypatch, settings):
    factory = Mock()
    monkeypatch.setattr("app.db.create_mongo_client", factory)
    connection = DatabaseConnection(settings)
    connection.open()
    connection.close()
    factory.assert_not_called()
    assert connection.database is None


def test_single_pool_is_reused_and_closed_once(monkeypatch):
    client = Mock()
    client.__getitem__ = Mock(return_value="database")
    factory = Mock(return_value=client)
    monkeypatch.setattr("app.db.create_mongo_client", factory)
    connection = DatabaseConnection(Settings(mongodb_uri="mongodb://unused", mongodb_database="test_db"))
    connection.open()
    connection.open()
    assert connection.database == "database"
    factory.assert_called_once()
    client.__getitem__.assert_called_once_with("test_db")
    connection.close()
    connection.close()
    client.close.assert_called_once()
    assert connection.client is None and connection.database is None


def test_initialization_error_does_not_log_secret_or_break_health(monkeypatch, caplog):
    secret_uri = "mongodb://user:secret-password@unused"
    monkeypatch.setattr("app.db.create_mongo_client", Mock(side_effect=RuntimeError(secret_uri)))
    connection = DatabaseConnection(Settings(mongodb_uri=secret_uri))
    connection.open()
    assert connection.database is None
    assert "secret-password" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_partial_initialization_closes_pool(monkeypatch):
    client = Mock()
    client.__getitem__ = Mock(side_effect=ValueError("invalid database"))
    monkeypatch.setattr("app.db.create_mongo_client", Mock(return_value=client))
    connection = DatabaseConnection(Settings(mongodb_uri="mongodb://unused"))
    connection.open()
    client.close.assert_called_once()
    assert connection.client is None and connection.database is None


def test_lifespan_closes_pool_and_preserves_health(monkeypatch):
    client = Mock()
    client.__getitem__ = Mock(return_value="database")
    monkeypatch.setattr("app.db.create_mongo_client", Mock(return_value=client))
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(mongodb_uri="mongodb://unused"))
    with TestClient(app) as api:
        assert api.get("/health").status_code == 200
        assert app.state.database_connection.database == "database"
    client.close.assert_called_once()
    assert app.state.database_connection.database is None


def test_missing_database_fails_explicitly():
    api = FastAPI()

    @api.get("/probe")
    def probe(database=Depends(get_database)):
        return {"ready": True}

    with TestClient(api) as client:
        response = client.get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "資料庫尚未設定完成或暫時無法使用"


def test_database_uri_is_excluded_from_settings_repr():
    assert "secret-password" not in repr(Settings(mongodb_uri="mongodb://user:secret-password@unused"))


def test_readonly_ping_does_not_write_or_create_indexes(monkeypatch, capsys):
    from scripts.check_mongodb import main
    client = Mock()
    monkeypatch.setattr("scripts.check_mongodb.get_settings", lambda: Settings(mongodb_uri="mongodb://unused"))
    client.__getitem__ = Mock(return_value=Mock())
    monkeypatch.setattr("app.db.create_mongo_client", Mock(return_value=client))
    assert main() == 0
    client.admin.command.assert_called_once_with("ping")
    client.close.assert_called_once()
    assert "No documents or indexes were written" in capsys.readouterr().out
    assert [call[0] for call in client.method_calls] == ["admin.command", "close"]
