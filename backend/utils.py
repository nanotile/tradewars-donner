"""Shared utilities — REPO_ROOT and UTC timestamp helper.

Consolidates definitions previously duplicated across arena.py,
trader.py, mcp_servers.py, and accounts.py.
"""

from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
