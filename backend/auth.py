"""
Module: JWT Authentication
Version: 1.0.0
Development Iteration: v1
Developer: Kent Benson

UV Environment: uv run uvicorn --factory backend.api.app:create_app --port 8000

Simple JWT auth with bcrypt passwords. Users stored in backend/data/users.json.
When AUTH_SECRET_KEY env var is unset, auth is completely disabled (dev mode).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

try:
    from jose import JWTError, jwt
except ImportError:
    jwt = None
    JWTError = Exception

try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _bcrypt = None
    _BCRYPT_AVAILABLE = False

import logging

logger = logging.getLogger(__name__)

AUTH_SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", "")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7

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
        json.dump(users, f, indent=2)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not _BCRYPT_AVAILABLE:
        return False
    return _bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def hash_password(password: str) -> str:
    if not _BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt not installed")
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
    if not jwt:
        raise RuntimeError("python-jose not installed")
    users = _load_users()
    jwt_version = users.get(username, {}).get("jwt_version", 0)
    expires_at = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": username,
        "exp": expires_at,
        "jv": jwt_version,
    }
    token = jwt.encode(payload, AUTH_SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at


def decode_token(token: str) -> Optional[str]:
    if not jwt:
        return None
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        token_version = payload.get("jv", 0)
        users = _load_users()
        current_version = users.get(username, {}).get("jwt_version", 0)
        if token_version < current_version:
            return None
        return username
    except JWTError:
        return None


def bump_jwt_version(username: str):
    users = _load_users()
    if username in users:
        users[username]["jwt_version"] = users[username].get("jwt_version", 0) + 1
        _save_users(users)
        logger.info("JWT version bumped for user '%s'", username)


def verify_auth(request: Request):
    if not AUTH_SECRET_KEY:
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
    if not AUTH_SECRET_KEY:
        return username

    users = _load_users()
    user = users.get(username, {})
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return username
