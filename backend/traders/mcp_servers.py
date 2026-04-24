"""Per-trader stdio MCP factories.

Each trader gets:
  - a Massive MCP (shared entry point, stateful per-trader SQLite only inside
    that trader's MCP process — so query_data tables don't cross-contaminate)
  - its own Memory MCP with an isolated JSONL storage file

Memory files are wiped by the arena at game start.
"""

from __future__ import annotations

import os
from pathlib import Path

from agents.mcp import MCPServerStdio

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = REPO_ROOT / "backend" / "environment" / "memory"

# First start of Massive MCP indexes the OpenAPI spec from llms-full.txt;
# the default 5s MCP init timeout is too short.
_MCP_INIT_TIMEOUT = 60


def memory_file_path(trader_id: str) -> Path:
    return MEMORY_DIR / f"trader_{trader_id}.jsonl"


def wipe_memory_files(trader_ids: list[str]) -> None:
    """Delete all per-trader memory JSONL files. Call at arena start."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for tid in trader_ids:
        p = memory_file_path(tid)
        if p.exists():
            p.unlink()


def make_massive_mcp() -> MCPServerStdio:
    return MCPServerStdio(
        name="Massive",
        params={
            "command": "mcp_massive",
            "args": [],
            "env": {**os.environ, "MASSIVE_API_KEY": os.environ["MASSIVE_API_KEY"]},
        },
        cache_tools_list=True,
        client_session_timeout_seconds=_MCP_INIT_TIMEOUT,
    )


def make_memory_mcp(trader_id: str) -> MCPServerStdio:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MCPServerStdio(
        name=f"Memory[{trader_id}]",
        params={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
            "env": {**os.environ, "MEMORY_FILE_PATH": str(memory_file_path(trader_id))},
        },
        cache_tools_list=True,
        client_session_timeout_seconds=_MCP_INIT_TIMEOUT,
    )
