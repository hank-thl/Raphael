from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure
from app.core.config import Settings, get_settings
from app.core.firebase import get_auth_service
from app.core.security import SESSION_COOKIE
from app.main import app
from test_auth import FakeAuth, csrf_headers, ORIGIN

class Store:
    def __init__(self):
        self.rows = {}
        self.fail = False
    def find_one(self, query):
        if self.fail: raise OperationFailure('private-error')
        return self.rows.get(query['_id'])
    def find(self, query):
        if self.fail: raise OperationFailure('private-error')
        return list(self.rows.values())
    def update_one(self, query, update, upsert=False):
        uid = query['_id']
        if uid not in self.rows:
            assert upsert
            self.rows[uid] = {'_id': uid, **update.get('$setOnInsert', {})}
        self.rows[uid].update(update['$set'])

@pytest.fixture
def ctx():
    settings = Settings(firebase_project_id='test', admin_uid='owner', allowed_uids=frozenset({'legacy'}), allowed_origins=frozenset({ORIGIN}), signup_restriction_confirmed=True, cookie_secure=False)
    service = FakeAuth()
    service.claims['uid'] = 'owner'
    service.ensure_account = lambda email: SimpleNamespace(uid='new', email=email)
    store = Store()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = lambda: service
    with TestClient(app) as client:
        app.state.database_connection.database = {'access_users': store}
        client.cookies.set(SESSION_COOKIE, 'signed-session')
        yield client, service, store
    app.dependency_overrides.clear()

def test_add_disable_reenable_and_no_silent_reenable(ctx):
    c, s, db = ctx
    r = c.get('/admin/users')
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    assert {a['uid'] for a in r.json()} == {'owner', 'legacy'}
    h = csrf_headers(c)
    assert c.post('/admin/users', json={'email':'a@b.c'}, headers=h).status_code == 200
    assert c.patch('/admin/users/new', json={'enabled':False}, headers=h).status_code == 200
    s.claims['uid'] = 'new'
    assert c.get('/auth/me').status_code == 403
    assert c.get('/records').status_code == 403
    assert c.post('/auth/login', json={'id_token':'valid'}, headers=h).status_code == 403
    s.claims['uid'] = 'owner'
    assert c.post('/admin/users', json={'email':'a@b.c'}, headers=h).json()['enabled'] is False
    assert c.patch('/admin/users/new', json={'enabled':True}, headers=h).status_code == 200
    s.claims['uid'] = 'new'
    assert c.get('/auth/me').status_code == 200

@pytest.mark.parametrize('uid', ['legacy','unknown'])
def test_non_admin_denied(ctx, uid):
    c,s,db = ctx
    s.claims['uid'] = uid
    assert c.get('/admin/users').status_code == 403
    assert c.post('/admin/users', json={'email':'a@b.c'}, headers=csrf_headers(c)).status_code == 403
    assert not db.rows

def test_owner_protected_and_legacy_override(ctx):
    c,s,db = ctx
    h = csrf_headers(c)
    assert c.patch('/admin/users/owner', json={'enabled':False}, headers=h).status_code == 403
    assert c.patch('/admin/users/legacy', json={'enabled':False}, headers=h).status_code == 200
    s.claims['uid'] = 'legacy'
    assert c.get('/auth/me').status_code == 403

def test_csrf_validation_and_anonymous(ctx):
    c,s,db = ctx
    assert c.post('/admin/users', json={'email':'a@b.c'}).status_code == 403
    h = csrf_headers(c)
    for body in [{'email':'bad'}, {'email':'a@b.c','is_admin':True}]:
        assert c.post('/admin/users', json=body, headers=h).status_code == 422
    assert c.patch('/admin/users/missing', json={'enabled':True}, headers=h).status_code == 404
    assert c.patch('/admin/users/legacy', json={'enabled':'false'}, headers=h).status_code == 422
    c.cookies.clear()
    assert c.get('/admin/users').status_code == 401
    assert not db.rows

def test_outage_fails_closed(ctx):
    c,s,db = ctx
    db.fail = True
    assert c.get('/admin/users').status_code == 503
    s.claims['uid'] = 'legacy'
    r = c.get('/auth/me')
    assert r.status_code == 503 and 'private-error' not in r.text

def test_firebase_failure_no_grant(ctx):
    c,s,db = ctx
    def fail(email): raise RuntimeError('secret-error')
    s.ensure_account = fail
    r = c.post('/admin/users', json={'email':'a@b.c'}, headers=csrf_headers(c))
    assert r.status_code == 503 and 'secret-error' not in r.text
    assert not db.rows

def test_database_failure_after_firebase_never_silently_succeeds(ctx):
    c,s,db = ctx
    def prepare(email):
        db.fail = True
        return SimpleNamespace(uid='new', email=email)
    s.ensure_account = prepare
    def fail_update(*args, **kwargs): raise OperationFailure('private-error')
    db.update_one = fail_update
    r = c.post('/admin/users', json={'email':'a@b.c'}, headers=csrf_headers(c))
    assert r.status_code == 503 and not db.rows
    assert 'private-error' not in r.text


def test_owner_can_login_without_legacy_list(ctx):
    from dataclasses import replace
    c,s,db = ctx
    settings = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: replace(settings, allowed_uids=frozenset())
    assert c.get('/auth/me').status_code == 200
