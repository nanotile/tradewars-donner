#!/usr/bin/env bash
# Module: stop.sh, Version 1.0.0, Iteration v1
# Developer: Kent Benson
# UV Environment: uv run (auto-managed)
#
# Stop the Tradewars backend gracefully.
#
# Usage:
#   scripts/stop.sh           Graceful stop (SIGTERM, 10s timeout, then SIGKILL)
#   scripts/stop.sh --force   Immediate SIGKILL

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPTS_DIR/.tradewars.pid"
GRACEFUL_TIMEOUT=10

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1"
}

FORCE=false
if [ "${1:-}" = "--force" ]; then
    FORCE=true
fi

if [ ! -f "$PID_FILE" ]; then
    log "(no PID file — tradewars is not running)"
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    log "(PID $PID is not running — cleaning up stale PID file)"
    rm -f "$PID_FILE"
    exit 0
fi

if [ "$FORCE" = true ]; then
    log "force-killing tradewars (PID $PID)..."
    kill -9 "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"
    log "tradewars killed."
    exit 0
fi

log "stopping tradewars (PID $PID)..."
kill "$PID" 2>/dev/null || true

for i in $(seq 1 "$GRACEFUL_TIMEOUT"); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        log "tradewars stopped gracefully."
        exit 0
    fi
    sleep 1
done

log "still running after ${GRACEFUL_TIMEOUT}s — sending SIGKILL..."
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
log "tradewars killed."
