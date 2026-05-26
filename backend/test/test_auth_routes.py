"""Tests for /api/auth/* and /api/admin/* endpoints with real JWT auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth_mod
from backend.api.app import ArenaHolder, create_app
from backend.arena.arena import DEFAULT_CONFIG_PATH
from backend.environment.accounts import Accounts
from backend.test.conftest import FakePrices

pytestmark = pytest.mark.usefixtures("neutralize_trader_loop")


@pytest.fixture(autouse=True)
def auth_env(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_mod, "AUTH_SECRET_KEY", "test-secret-key-12345")
    monkeypatch.setattr(auth_mod, "DEV_MODE", False)
    monkeypatch.setattr(auth_mod, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth_mod, "_users_cache", None)
    monkeypatch.setattr(auth_mod, "_users_mtime", 0.0)
    auth_mod._login_failures.clear()
    auth_mod._lockout_until.clear()


@pytest.fixture
def _seed_user():
    users = {
        "testuser@example.com": {
            "display_name": "Test User",
            "password_hash": auth_mod.hash_password("correct-password"),
            "is_admin": False,
            "jwt_version": 0,
        }
    }
    auth_mod.save_users(users)


@pytest.fixture
def _seed_admin():
    users = {
        "admin@example.com": {
            "display_name": "Admin",
            "password_hash": auth_mod.hash_password("admin-password"),
            "is_admin": True,
            "jwt_version": 0,
        }
    }
    auth_mod.save_users(users)


@pytest.fixture
def holder(tmp_path):
    h = ArenaHolder.__new__(ArenaHolder)
    h.config_path = DEFAULT_CONFIG_PATH
    h.db_path = tmp_path / "test.sqlite"
    h.accounts = Accounts(":memory:")
    h.prices = FakePrices({})
    h.arena = None
    yield h
    h.accounts.close()


@pytest.fixture
def client(holder):
    app = create_app(holder=holder)
    app.state.limiter.enabled = False
    with TestClient(app) as c:
        yield c


# ---- POST /api/auth/login ----

def test_login_success(client, _seed_user):
    r = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["username"] == "testuser@example.com"
    assert body["display_name"] == "Test User"
    assert body["expires_at"]


def test_login_wrong_password(client, _seed_user):
    r = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "wrong-password",
    })
    assert r.status_code == 401


def test_login_nonexistent_user(client):
    r = client.post("/api/auth/login", json={
        "username": "nobody@example.com",
        "password": "anything",
    })
    assert r.status_code == 401


def test_login_normalizes_username(client, _seed_user):
    r = client.post("/api/auth/login", json={
        "username": "  TestUser@EXAMPLE.com  ",
        "password": "correct-password",
    })
    assert r.status_code == 200
    assert r.json()["username"] == "testuser@example.com"


# ---- GET /api/auth/me ----

def test_me_with_valid_token(client, _seed_user):
    login = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    }).json()
    r = client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {login['token']}",
    })
    assert r.status_code == 200
    assert r.json()["username"] == "testuser@example.com"


def test_me_without_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_invalid_token(client):
    r = client.get("/api/auth/me", headers={
        "Authorization": "Bearer garbage-token",
    })
    assert r.status_code == 401


# ---- POST /api/auth/change-password ----

def test_change_password(client, _seed_user):
    login = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    }).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    r = client.post("/api/auth/change-password", json={
        "current_password": "correct-password",
        "new_password": "new-secure-password",
    }, headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "new-secure-password",
    })
    assert r2.status_code == 200


def test_change_password_wrong_current(client, _seed_user):
    login = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    }).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    r = client.post("/api/auth/change-password", json={
        "current_password": "wrong",
        "new_password": "new-secure-password",
    }, headers=headers)
    assert r.status_code == 400


# ---- POST /api/auth/sse-ticket ----

def test_sse_ticket(client, _seed_user):
    login = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    }).json()
    r = client.post("/api/auth/sse-ticket", headers={
        "Authorization": f"Bearer {login['token']}",
    })
    assert r.status_code == 200
    assert r.json()["ticket"]


# ---- Lockout ----

def test_lockout_after_repeated_failures(client, _seed_user):
    for _ in range(5):
        client.post("/api/auth/login", json={
            "username": "testuser@example.com",
            "password": "wrong",
        })

    r = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    })
    assert r.status_code == 429


# ---- Admin routes ----

def test_admin_list_users(client, _seed_admin):
    login = client.post("/api/auth/login", json={
        "username": "admin@example.com",
        "password": "admin-password",
    }).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    r = client.get("/api/admin/users", headers=headers)
    assert r.status_code == 200
    users = r.json()
    assert len(users) == 1
    assert users[0]["username"] == "admin@example.com"


def test_admin_create_and_delete_user(client, _seed_admin):
    login = client.post("/api/auth/login", json={
        "username": "admin@example.com",
        "password": "admin-password",
    }).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    r = client.post("/api/admin/users", json={
        "username": "newuser@example.com",
        "display_name": "New User",
        "password": "new-password-123",
    }, headers=headers)
    assert r.status_code == 200
    assert r.json()["username"] == "newuser@example.com"

    users = client.get("/api/admin/users", headers=headers).json()
    assert len(users) == 2

    r = client.delete("/api/admin/users/newuser@example.com", headers=headers)
    assert r.status_code == 200

    users = client.get("/api/admin/users", headers=headers).json()
    assert len(users) == 1


def test_non_admin_cannot_list_users(client, _seed_user):
    login = client.post("/api/auth/login", json={
        "username": "testuser@example.com",
        "password": "correct-password",
    }).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    r = client.get("/api/admin/users", headers=headers)
    assert r.status_code == 403


# ---- DEV_MODE admin gate ----

def test_dev_mode_without_dev_admin_blocks_admin_routes(monkeypatch, holder):
    monkeypatch.setattr(auth_mod, "AUTH_SECRET_KEY", "")
    monkeypatch.setattr(auth_mod, "DEV_MODE", True)
    monkeypatch.setattr(auth_mod, "DEV_ADMIN", False)
    app = create_app(holder=holder)
    app.state.limiter.enabled = False
    with TestClient(app) as c:
        assert c.get("/api/admin/users").status_code == 403
        assert c.post("/arena/start").status_code == 403
        assert c.post("/arena/stop").status_code == 403


def test_dev_mode_with_dev_admin_allows_admin_routes(monkeypatch, holder):
    monkeypatch.setattr(auth_mod, "AUTH_SECRET_KEY", "")
    monkeypatch.setattr(auth_mod, "DEV_MODE", True)
    monkeypatch.setattr(auth_mod, "DEV_ADMIN", True)
    app = create_app(holder=holder)
    app.state.limiter.enabled = False
    with TestClient(app) as c:
        assert c.get("/api/admin/users").status_code == 200
