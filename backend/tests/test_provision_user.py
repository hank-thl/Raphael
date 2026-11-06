from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.provision_user import ensure_user, main


class NotFound(Exception):
    pass


def test_preview_does_not_initialize_sdk(monkeypatch, capsys):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test")
    assert main(["--email", "owner@example.com", "--project-id", "test"]) == 0
    assert "No Firebase request made" in capsys.readouterr().out


def test_wrong_project_rejected_before_apply(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "expected")
    with pytest.raises(SystemExit):
        main(["--email", "owner@example.com", "--project-id", "wrong", "--apply"])


def test_existing_user_is_not_modified():
    user = SimpleNamespace(uid="existing", disabled=False)
    auth = Mock(UserNotFoundError=NotFound)
    auth.get_user_by_email.return_value = user
    assert ensure_user("owner@example.com", auth, "app") == (user, False)
    auth.create_user.assert_not_called()
    auth.update_user.assert_not_called()


def test_create_without_password_or_fabricated_provider_identity():
    user = SimpleNamespace(uid="new", disabled=False)
    auth = Mock(UserNotFoundError=NotFound)
    auth.get_user_by_email.side_effect = NotFound()
    auth.create_user.return_value = user
    assert ensure_user("owner@example.com", auth, "app") == (user, True)
    auth.create_user.assert_called_once_with(email="owner@example.com", email_verified=False,
                                            disabled=False, app="app")


def test_disabled_account_is_not_reenabled():
    auth = Mock(UserNotFoundError=NotFound)
    auth.get_user_by_email.return_value = SimpleNamespace(disabled=True)
    with pytest.raises(RuntimeError):
        ensure_user("owner@example.com", auth, "app")
    auth.update_user.assert_not_called()
    auth.create_user.assert_not_called()


def test_remote_errors_do_not_trigger_creation():
    auth = Mock(UserNotFoundError=NotFound)
    auth.get_user_by_email.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        ensure_user("owner@example.com", auth, "app")
    auth.create_user.assert_not_called()
