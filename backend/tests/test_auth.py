import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.firebase import InvalidCredential, get_auth_service
from app.core.security import CSRF_COOKIE, SESSION_COOKIE
from app.main import app

ORIGIN = "http://localhost:3000"


class FakeAuth:
    """Models already verified claims; does not test Firebase cryptography."""
    def __init__(self):
        self.claims = {"uid": "allowed", "email": "test@example.com", "auth_time": time.time(),
                       "firebase": {"sign_in_provider": "google.com"}}
        self.error = None
        self.created = False

    def verify_id_token(self, token):
        if self.error:
            raise self.error
        if token != "valid":
            raise InvalidCredential()
        return self.claims

    def create_session_cookie(self, token, seconds):
        self.created = True
        self.session_seconds = seconds
        return "signed-session"

    def verify_session_cookie(self, cookie):
        if self.error:
            raise self.error
        if cookie != "signed-session":
            raise InvalidCredential()
        return self.claims


@pytest.fixture
def context():
    settings = Settings(firebase_project_id="test", allowed_uids=frozenset({"allowed"}),
                        allowed_origins=frozenset({ORIGIN}), signup_restriction_confirmed=True,
                        cookie_secure=False)
    service = FakeAuth()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = lambda: service
    with TestClient(app) as client:
        yield client, service, settings
    app.dependency_overrides.clear()


def csrf_headers(client):
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def test_google_login_me_and_local_logout(context):
    client, service, _ = context
    assert client.get("/auth/me").status_code == 401
    headers = csrf_headers(client)
    response = client.post("/auth/login", json={"id_token": "valid"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"uid": "allowed", "email": "test@example.com"}
    assert service.created
    assert service.session_seconds == 1209600
    assert "Max-Age=1209600" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("origin,token", [(None, None), (ORIGIN, None),
    ("https://evil.example", "correct"), (ORIGIN, "wrong"), (ORIGIN, "é")])
@pytest.mark.parametrize("path", ["/auth/login", "/auth/logout"])
def test_write_routes_reject_missing_or_wrong_origin_and_csrf(context, origin, token, path):
    client, service, _ = context
    actual = csrf_headers(client)["x-csrf-token"]
    headers = {}
    if origin:
        headers["origin"] = origin
    if token:
        # HTTP headers use bytes for a non-ASCII adversarial value.
        headers["x-csrf-token"] = actual if token == "correct" else token.encode()
    response = client.post(path, json={"id_token": "valid"}, headers=headers)
    assert response.status_code == 403
    assert not service.created


@pytest.mark.parametrize("claims", [
    {"uid": "someone-else"},
    {"firebase": {"sign_in_provider": "password"}},
    {"firebase": None},
])
def test_valid_identity_is_not_enough_for_authorization(context, claims):
    client, service, _ = context
    service.claims.update(claims)
    response = client.post("/auth/login", json={"id_token": "valid"}, headers=csrf_headers(client))
    assert response.status_code == 403
    assert not service.created


@pytest.mark.parametrize("offset", [-600, 600])
def test_login_requires_recent_authentication(context, offset):
    client, service, _ = context
    service.claims["auth_time"] = time.time() + offset
    response = client.post("/auth/login", json={"id_token": "valid"}, headers=csrf_headers(client))
    assert response.status_code == 401
    assert not service.created


@pytest.mark.parametrize("error,status", [(InvalidCredential(), 401), (RuntimeError("secret-detail"), 503)])
def test_invalid_or_unavailable_verifier_never_creates_session(context, error, status):
    client, service, _ = context
    service.error = error
    response = client.post("/auth/login", json={"id_token": "valid"}, headers=csrf_headers(client))
    assert response.status_code == status
    assert "secret-detail" not in response.text
    assert not service.created


@pytest.mark.parametrize("cookie", ["forged", "expired", "revoked", "disabled"])
def test_invalid_session_is_rejected(context, cookie):
    client, _, _ = context
    client.cookies.set(SESSION_COOKIE, cookie)
    assert client.get("/auth/me").status_code == 401


def test_removing_uid_blocks_existing_session(context):
    client, service, _ = context
    client.cookies.set(SESSION_COOKIE, "signed-session")
    service.claims["uid"] = "removed"
    assert client.get("/auth/me").status_code == 403


@pytest.mark.parametrize("change", [
    {"allowed_uids": frozenset()}, {"firebase_project_id": ""},
    {"allowed_origins": frozenset()}, {"signup_restriction_confirmed": False},
])
def test_incomplete_setup_fails_closed_without_breaking_health(context, change):
    client, _, settings = context
    app.dependency_overrides[get_settings] = lambda: replace(settings, **change)
    assert client.get("/auth/csrf").status_code == 503
    assert client.get("/auth/me").status_code == 503
    assert client.post("/auth/login", json={"id_token": "valid"}).status_code == 503
    assert client.get("/health").status_code == 200


def test_client_cannot_supply_its_own_uid(context):
    client, service, _ = context
    response = client.post("/auth/login", json={"id_token": "valid", "uid": "allowed"},
                           headers=csrf_headers(client))
    assert response.status_code == 422
    assert not service.created


def test_secure_cookie_attributes(context):
    client, _, settings = context
    app.dependency_overrides[get_settings] = lambda: replace(settings, cookie_secure=True)
    cookie = client.get("/auth/csrf").headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_default_configuration_refuses_all_accounts(monkeypatch):
    for key in ["FIREBASE_PROJECT_ID", "ALLOWED_FIREBASE_UIDS", "AUTH_ALLOWED_ORIGINS",
                "AUTH_SIGNUP_RESTRICTION_CONFIRMED", "COOKIE_SECURE"]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert not settings.auth_ready
    assert settings.cookie_secure
    get_settings.cache_clear()


@pytest.mark.parametrize("stage", ["verify_id_token", "create_session_cookie"])
def test_login_outage_logs_phase_without_credentials(context, caplog, stage):
    client, service, _ = context
    def fail(*args):
        raise RuntimeError("secret-token-and-private-key")
    setattr(service, stage, fail)
    with caplog.at_level("WARNING"):
        response = client.post("/auth/login", json={"id_token": "valid"}, headers=csrf_headers(client))
    assert response.status_code == 503
    assert SESSION_COOKIE not in client.cookies
    assert f"stage={stage} exception=RuntimeError" in caplog.text
    assert "secret-token-and-private-key" not in caplog.text + response.text


def test_initialization_logs_only_stage_and_type(monkeypatch, caplog):
    from fastapi import HTTPException
    from app.core import firebase
    settings = Settings(firebase_project_id="test", allowed_uids=frozenset({"allowed"}),
                        allowed_origins=frozenset({ORIGIN}), signup_restriction_confirmed=True)
    def fail(project_id):
        raise RuntimeError("private-credential-path-and-token")
    monkeypatch.setattr(firebase, "firebase_service", fail)
    with caplog.at_level("WARNING"), pytest.raises(HTTPException) as exc:
        firebase.get_auth_service(settings)
    assert exc.value.status_code == 503
    assert "stage=firebase_initialize exception=RuntimeError" in caplog.text
    assert "private-credential-path-and-token" not in caplog.text


def test_session_outage_logs_phase_without_cookie_or_error_message(context, caplog):
    client, service, _ = context
    client.cookies.set(SESSION_COOKIE, 'signed-session')
    service.error = RuntimeError('private-session-and-key')
    with caplog.at_level('WARNING'):
        response = client.get('/auth/me')
    assert response.status_code == 503
    assert 'stage=verify_session_cookie exception=RuntimeError' in caplog.text
    assert 'private-session-and-key' not in caplog.text + response.text
    assert 'signed-session' not in caplog.text


@pytest.mark.parametrize("value", [None, "1209600"])
def test_fourteen_day_session_configuration(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SESSION_SECONDS", raising=False)
    else:
        monkeypatch.setenv("SESSION_SECONDS", value)
    get_settings.cache_clear()
    assert get_settings().session_seconds == 1209600
    assert Settings().session_seconds == 1209600


def test_session_duration_cannot_exceed_firebase_limit(monkeypatch):
    monkeypatch.setenv("SESSION_SECONDS", "2592000")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()
