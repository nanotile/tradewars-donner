"""
Module: Auth API Routes
Version: 1.0.0
Development Iteration: v1
Developer: Kent Benson

UV Environment: uv run uvicorn --factory backend.api.app:create_app --port 8000

Login endpoint + token validation. Rate-limited to 5/min on login.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

import backend.auth as _auth_mod

logger = logging.getLogger(__name__)
from backend.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    create_sse_ticket,
    decode_token,
    decode_refresh_token,
    bump_jwt_version,
    check_lockout,
    record_login_failure,
    load_users,
    save_users,
    verify_password,
    hash_password,
    verify_auth,
)

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=200)


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest):
    if _auth_mod.DEV_MODE and not _auth_mod.AUTH_SECRET_KEY:
        return {
            "token": "",
            "username": "dev",
            "display_name": "Dev Mode",
            "expires_at": None,
            "auth_disabled": True,
        }

    client_ip = request.client.host if request.client else "unknown"
    normalized = body.username.strip().lower()
    check_lockout(normalized)
    user = authenticate_user(body.username, body.password)
    if not user:
        record_login_failure(normalized)
        logger.warning("Failed login attempt for '%s' from %s", body.username, client_ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    logger.info("Successful login for '%s' from %s", user["username"], client_ip)
    token, expires_at = create_access_token(user["username"])
    refresh, refresh_expires = create_refresh_token(user["username"])
    return {
        "token": token,
        "refresh_token": refresh,
        "username": user["username"],
        "display_name": user["display_name"],
        "is_admin": user.get("is_admin", False),
        "expires_at": expires_at.isoformat(),
        "refresh_expires_at": refresh_expires.isoformat(),
    }


@router.get("/me")
async def me(request: Request):
    if _auth_mod.DEV_MODE and not _auth_mod.AUTH_SECRET_KEY:
        return {
            "username": "dev",
            "display_name": "Dev Mode",
            "auth_disabled": True,
        }

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]
    username = decode_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    users = load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return {
        "username": username,
        "display_name": user.get("display_name", username),
        "is_admin": user.get("is_admin", False),
    }


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., min_length=6, max_length=200)


@router.post("/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    username: str = Depends(verify_auth),
):
    if _auth_mod.DEV_MODE and not _auth_mod.AUTH_SECRET_KEY:
        return {"ok": True, "message": "Auth disabled in dev mode"}

    users = load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    users[username]["password_hash"] = hash_password(body.new_password)
    save_users(users)
    bump_jwt_version(username)
    return {"ok": True, "message": "Password changed - all other sessions have been signed out"}


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., max_length=2000)


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    if _auth_mod.DEV_MODE and not _auth_mod.AUTH_SECRET_KEY:
        return {"token": "", "refresh_token": "", "expires_at": None, "auth_disabled": True}

    username = decode_refresh_token(body.refresh_token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    users = load_users()
    if username not in users:
        raise HTTPException(status_code=401, detail="User no longer exists")

    token, expires_at = create_access_token(username)
    new_refresh, refresh_expires = create_refresh_token(username)
    return {
        "token": token,
        "refresh_token": new_refresh,
        "expires_at": expires_at.isoformat(),
        "refresh_expires_at": refresh_expires.isoformat(),
    }


@router.post("/sse-ticket")
async def sse_ticket(request: Request, username: str = Depends(verify_auth)):
    ticket = create_sse_ticket(username)
    return {"ticket": ticket}
