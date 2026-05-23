"""
Module: Admin User Management Routes
Version: 1.0.0
Development Iteration: v1
Developer: Kent Benson

UV Environment: uv run uvicorn --factory backend.api.app:create_app --port 8000

CRUD endpoints for managing dashboard users. All routes require admin access.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth import verify_admin, _load_users, _save_users, hash_password

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@+\-]+$")

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str = Field(..., max_length=100, pattern=r"^[a-zA-Z0-9_.@+\-]+$")
    display_name: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6, max_length=200)


@router.get("/users")
async def list_users(request: Request):
    verify_admin(request)
    users = _load_users()
    return [
        {
            "username": uname,
            "display_name": data.get("display_name", uname),
            "is_admin": data.get("is_admin", False),
        }
        for uname, data in users.items()
    ]


@router.post("/users")
async def create_user(request: Request, body: CreateUserRequest):
    verify_admin(request)
    username = body.username.strip().lower()
    users = _load_users()

    if username in users:
        raise HTTPException(status_code=409, detail="User already exists")

    users[username] = {
        "display_name": body.display_name,
        "password_hash": hash_password(body.password),
        "is_admin": False,
    }
    _save_users(users)
    return {
        "username": username,
        "display_name": body.display_name,
        "is_admin": False,
    }


@router.delete("/users/{username}")
async def delete_user(request: Request, username: str):
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Invalid username format")

    admin = verify_admin(request)

    if username == admin:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")

    del users[username]
    _save_users(users)
    return {"deleted": username}
