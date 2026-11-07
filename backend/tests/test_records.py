import base64
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from app.api.records import get_records_repository
from app.core.config import Settings, get_settings
from app.core.firebase import InvalidCredential, get_auth_service
from app.core.security import SESSION_COOKIE
from app.main import app
from app.repositories.records import RecordsRepository

ORIGIN = "http://localhost:3000"
STAMP = datetime(2026, 11, 7, tzinfo=timezone.utc)


def matches(row, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(row, branch) for branch in value):
                return False
        elif isinstance(value, dict) and "$lt" in value:
            if not row[key] < value["$lt"]:
                return False
        elif row.get(key) != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, fields):
        for key, direction in reversed(fields):
            self.rows.sort(key=lambda row: row[key], reverse=direction == -1)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(self.rows)


class Collection:
    """A small in-memory query fake, not an Atlas/MongoDB integration test."""
    def __init__(self):
        self.rows = []
        self.queries = []
        self.error = None

    def insert_one(self, document):
        if self.error:
            raise self.error
        self.rows.append(copy.deepcopy(document))
        return SimpleNamespace(inserted_id=document["_id"])

    def find(self, query):
        if self.error:
            raise self.error
        self.queries.append(copy.deepcopy(query))
        return Cursor([copy.deepcopy(row) for row in self.rows if matches(row, query)])

    def update_one(self, query, update):
        if self.error:
            raise self.error
        self.queries.append(copy.deepcopy(query))
        for row in self.rows:
            if matches(row, query):
                row.update(update["$set"])
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def seed(self, uid, number, deleted=False):
        row = {"_id": ObjectId(f"{number:024x}"), "user_uid": uid, "content": f"{uid}-{number}",
               "created_at": STAMP, "deleted_at": STAMP if deleted else None}
        self.rows.append(row)
        return row


class Verifier:
    def verify_session_cookie(self, cookie):
        identities = {"session-a": "user-a", "session-b": "user-b", "session-c": "not-allowed"}
        if cookie not in identities:
            raise InvalidCredential()
        return {"uid": identities[cookie], "firebase": {"sign_in_provider": "google.com"}}


@pytest.fixture
def context():
    settings = Settings(firebase_project_id="test", allowed_uids=frozenset({"user-a", "user-b"}),
                        allowed_origins=frozenset({ORIGIN}), signup_restriction_confirmed=True,
                        cookie_secure=False)
    collection = Collection()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = lambda: Verifier()
    app.dependency_overrides[get_records_repository] = lambda: RecordsRepository(collection)
    with TestClient(app) as client:
        yield client, collection
    app.dependency_overrides.clear()


def login_as(client, who="a"):
    client.cookies.set(SESSION_COOKIE, f"session-{who}")


def write_headers(client):
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def test_create_uses_verified_uid_and_server_times_preserving_text(context):
    client, collection = context
    login_as(client)
    content = "  今天的紀錄\n第二行  "
    response = client.post("/records", json={"content": content}, headers=write_headers(client))
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == {"id", "content", "created_at"}
    assert data["content"] == content
    assert ObjectId.is_valid(data["id"])
    document = collection.rows[0]
    assert document["user_uid"] == "user-a"
    assert document["deleted_at"] is None
    assert document["created_at"].utcoffset().total_seconds() == 0


@pytest.mark.parametrize("content", ["", " \t\n", "x" * 10001, 1, True, None, {"$ne": None}])
def test_invalid_content_never_writes(context, content):
    client, collection = context
    login_as(client)
    response = client.post("/records", json={"content": content}, headers=write_headers(client))
    assert response.status_code == 422
    assert not collection.rows


def test_maximum_length_is_accepted(context):
    client, _ = context
    login_as(client)
    assert client.post("/records", json={"content": "字" * 10000}, headers=write_headers(client)).status_code == 201


@pytest.mark.parametrize("extra", [{"user_uid": "user-b"}, {"created_at": "2000-01-01"},
                                  {"deleted_at": None}, {"_id": "custom"}])
def test_client_cannot_supply_ownership_or_server_fields(context, extra):
    client, collection = context
    login_as(client)
    response = client.post("/records", json={"content": "hello", **extra}, headers=write_headers(client))
    assert response.status_code == 422
    assert not collection.rows


@pytest.mark.parametrize("cookie,status", [(None, 401), ("forged", 401), ("session-c", 403)])
@pytest.mark.parametrize("method,path", [("GET", "/records"), ("POST", "/records"),
    ("DELETE", "/records/000000000000000000000001")])
def test_unauthorized_requests_are_rejected_before_database_access(context, cookie, status, method, path):
    client, collection = context
    if cookie:
        client.cookies.set(SESSION_COOKIE, cookie)
    response = client.request(method, path, json={"content": "test"})
    assert response.status_code == status
    assert not collection.rows and not collection.queries
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method,path", [("POST", "/records"), ("DELETE", "/records/000000000000000000000001")])
@pytest.mark.parametrize("kind", ["missing", "wrong-token", "wrong-origin"])
def test_writes_require_csrf_and_origin(context, method, path, kind):
    client, collection = context
    login_as(client)
    headers = write_headers(client)
    if kind == "missing":
        headers.pop("x-csrf-token")
    elif kind == "wrong-token":
        headers["x-csrf-token"] = "wrong"
    else:
        headers["origin"] = "https://evil.example"
    response = client.request(method, path, json={"content": "test"}, headers=headers)
    assert response.status_code == 403
    assert not collection.rows and not collection.queries


def test_history_is_scoped_and_soft_deleted_records_are_hidden(context):
    client, collection = context
    collection.seed("user-a", 1)
    collection.seed("user-a", 2, deleted=True)
    collection.seed("user-b", 3)
    login_as(client)
    response = client.get("/records?user_uid=user-b&include_deleted=true")
    assert response.status_code == 200
    assert [row["content"] for row in response.json()["items"]] == ["user-a-1"]
    assert collection.queries[-1] == {"user_uid": "user-a", "deleted_at": None}
    assert response.headers["cache-control"] == "no-store"


def test_cursor_pagination_is_stable_for_equal_timestamps_and_new_inserts(context):
    client, collection = context
    for number in range(1, 27):
        collection.seed("user-a", number)
    collection.seed("user-b", 100)
    collection.seed("user-a", 101, deleted=True)
    login_as(client)
    first = client.get("/records").json()
    assert len(first["items"]) == 20
    assert first["items"][0]["content"] == "user-a-26"
    collection.seed("user-a", 200)  # Added between requests; must not duplicate old results.
    second = client.get("/records", params={"cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 6
    assert second["next_cursor"] is None
    ids = [row["id"] for row in first["items"] + second["items"]]
    assert len(set(ids)) == 26
    assert all(row["content"].startswith("user-a-") for row in second["items"])


def test_cursor_remains_usable_when_boundary_record_is_soft_deleted(context):
    client, collection = context
    for number in range(1, 5):
        collection.seed("user-a", number)
    login_as(client)
    first = client.get("/records?limit=2").json()
    assert client.delete("/records/" + first["items"][-1]["id"], headers=write_headers(client)).status_code == 204
    second = client.get("/records", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [row["content"] for row in second["items"]] == ["user-a-2", "user-a-1"]


@pytest.mark.parametrize("cursor", ["invalid", "", base64.urlsafe_b64encode(b'{}').decode(),
    base64.urlsafe_b64encode(json.dumps(["2026-11-07T00:00:00+00:00", "invalid-id"]).encode()).decode(),
    base64.urlsafe_b64encode(json.dumps(["2026-11-07", "000000000000000000000001"]).encode()).decode()])
def test_invalid_cursor_returns_400(context, cursor):
    client, _ = context
    login_as(client)
    assert client.get("/records", params={"cursor": cursor}).status_code == 400


@pytest.mark.parametrize("limit", [0, -1, 101, "no"])
def test_invalid_page_size_is_rejected(context, limit):
    client, _ = context
    login_as(client)
    assert client.get("/records", params={"limit": limit}).status_code == 422


def test_soft_delete_preserves_document_and_hides_it_from_history(context):
    client, collection = context
    row = collection.seed("user-a", 1)
    original = copy.deepcopy(row)
    login_as(client)
    headers = write_headers(client)
    response = client.delete("/records/" + str(row["_id"]), headers=headers)
    assert response.status_code == 204 and response.content == b""
    assert len(collection.rows) == 1
    assert row["content"] == original["content"] and row["created_at"] == original["created_at"]
    assert row["deleted_at"] is not None
    assert client.get("/records").json()["items"] == []
    assert client.delete("/records/" + str(row["_id"]), headers=headers).status_code == 404


def test_other_users_and_missing_or_deleted_records_all_return_same_404(context):
    client, collection = context
    other = collection.seed("user-b", 1)
    deleted = collection.seed("user-a", 2, deleted=True)
    login_as(client)
    headers = write_headers(client)
    responses = [client.delete("/records/" + record_id, headers=headers) for record_id in
        [str(other["_id"]), str(deleted["_id"]), str(ObjectId()), "invalid-id"]]
    assert all(response.status_code == 404 for response in responses)
    assert len({response.text for response in responses}) == 1
    assert other["deleted_at"] is None


@pytest.mark.parametrize("method,path", [("GET", "/records"), ("POST", "/records"),
    ("DELETE", "/records/000000000000000000000001")])
def test_database_errors_do_not_leak_credentials(context, method, path, caplog):
    client, collection = context
    login_as(client)
    collection.error = OperationFailure("mongodb://user:private-password@host")
    response = client.request(method, path, json={"content": "test"}, headers=write_headers(client))
    assert response.status_code == 503
    assert "private-password" not in response.text + caplog.text


def test_missing_database_is_explicit_503_after_authentication(context):
    client, _ = context
    app.dependency_overrides.pop(get_records_repository)
    login_as(client)
    assert client.get("/records").status_code == 503


def test_cursor_from_another_user_does_not_change_ownership_scope(context):
    client, collection = context
    for number in [5, 10, 15]:
        collection.seed("user-a", number)
    for number in [1, 2]:
        collection.seed("user-b", number)
    login_as(client)
    cursor = client.get("/records?limit=1").json()["next_cursor"]
    login_as(client, "b")
    response = client.get("/records", params={"cursor": cursor})
    assert [row["content"] for row in response.json()["items"]] == ["user-b-2", "user-b-1"]
