"""
Module: JWT Authentication
Version: 2.0.0
Development Iteration: v2
Developer: Kent Benson

UV Environment: uv run uvicorn --factory backend.api.app:create_app --port 8000

JWT auth with bcrypt passwords. Users stored in backend/data/users.json.
Requires AUTH_SECRET_KEY env var unless DEV_MODE=true.
"""

import fcntl
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt as _bcrypt
import jwt as _pyjwt
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

AUTH_SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", "")
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7


def check_auth_config() -> None:
    """Raise at startup if auth is misconfigured. Called from create_app()."""
    if not AUTH_SECRET_KEY and not DEV_MODE:
        raise RuntimeError(
            "AUTH_SECRET_KEY is not set. Set it in .env or pass DEV_MODE=true to disable auth."
        )
    if DEV_MODE and not AUTH_SECRET_KEY:
        logger.warning("AUTH DISABLED — DEV_MODE is set with no AUTH_SECRET_KEY")

USERS_FILE = Path(__file__).resolve().parent / "data" / "users.json"


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read users.json: %s", e)
        return {}


def _save_users(users: dict):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(users, f, indent=2)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def authenticate_user(username: str, password: str) -> Optional[dict]:
    username = username.strip().lower()
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return {
        "username": username,
        "display_name": user.get("display_name", username),
        "is_admin": user.get("is_admin", False),
    }


def create_access_token(username: str) -> tuple[str, datetime]:
    users = _load_users()
    jwt_version = users.get(username, {}).get("jwt_version", 0)
    expires_at = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": username,
        "exp": expires_at,
        "jv": jwt_version,
    }
    token = _pyjwt.encode(payload, AUTH_SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at


def decode_token(token: str) -> Optional[str]:
    try:
        payload = _pyjwt.decode(token, AUTH_SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        token_version = payload.get("jv", 0)
        users = _load_users()
        current_version = users.get(username, {}).get("jwt_version", 0)
        if token_version < current_version:
            return None
        return username
    except _pyjwt.PyJWTError:
        return None


def bump_jwt_version(username: str):
    users = _load_users()
    if username in users:
        users[username]["jwt_version"] = users[username].get("jwt_version", 0) + 1
        _save_users(users)
        logger.info("JWT version bumped for user '%s'", username)


def verify_auth(request: Request):
    if DEV_MODE and not AUTH_SECRET_KEY:
        return "dev"

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]
    username = decode_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return username


def verify_admin(request: Request) -> str:
    username = verify_auth(request)
    if DEV_MODE and not AUTH_SECRET_KEY:
        return username

    users = _load_users()
    user = users.get(username, {})
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return username


# ---------------------------------------------------------------------------
# SSE tickets — short-lived, single-use tokens for EventSource connections
# ---------------------------------------------------------------------------
_SSE_TICKET_TTL = timedelta(seconds=30)
_sse_tickets: dict[str, tuple[str, datetime]] = {}  # ticket → (username, expires_at)
_ticket_lock = threading.Lock()


def create_sse_ticket(username: str) -> str:
    ticket = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + _SSE_TICKET_TTL
    with _ticket_lock:
        _sse_tickets[ticket] = (username, expires)
    return ticket


def consume_sse_ticket(ticket: str) -> Optional[str]:
    now = datetime.now(timezone.utc)
    with _ticket_lock:
        entry = _sse_tickets.pop(ticket, None)
    if entry is None:
        return None
    username, expires = entry
    if now > expires:
        return None
    return username


# ---------------------------------------------------------------------------
# Login lockout — 5 failures in 10 min → 15 min lockout per username
# ---------------------------------------------------------------------------
_LOCKOUT_FAILURES = 5
_LOCKOUT_WINDOW = timedelta(minutes=10)
_LOCKOUT_DURATION = timedelta(minutes=15)
_login_failures: dict[str, list[datetime]] = {}  # username → [timestamps]
_lockout_until: dict[str, datetime] = {}  # username → locked_until


def check_lockout(username: str) -> None:
    now = datetime.now(timezone.utc)
    until = _lockout_until.get(username)
    if until and now < until:
        raise HTTPException(status_code=429, detail="Account temporarily locked")
    if until and now >= until:
        del _lockout_until[username]
        _login_failures.pop(username, None)


def record_login_failure(username: str) -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - _LOCKOUT_WINDOW
    attempts = _login_failures.get(username, [])
    attempts = [t for t in attempts if t > cutoff]
    attempts.append(now)
    _login_failures[username] = attempts
    if len(attempts) >= _LOCKOUT_FAILURES:
        _lockout_until[username] = now + _LOCKOUT_DURATION
        logger.warning("Account '%s' locked out after %d failed attempts", username, len(attempts))
