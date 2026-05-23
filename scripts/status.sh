#!/usr/bin/env bash
# Module: status.sh, Version 1.0.0, Iteration v1
# Developer: Kent Benson
# UV Environment: uv run (auto-managed)
#
# Check the status of the Tradewars backend.
#
# Usage:
#   scripts/status.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPTS_DIR/.tradewars.pid"
LOG_FILE="$ROOT/logs/tradewars.log"
PORT=5060
TUNNEL_URL="https://tradewars.kentbenson.net"

echo "=== Tradewars Status ==="
echo ""

RUNNING=false
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        RUNNING=true
        UPTIME=$(ps -o etime= -p "$PID" 2>/dev/null | tr -d ' ')
        echo "Process:     RUNNING (PID $PID)"
        echo "Uptime:      $UPTIME"
    else
        echo "Process:     DEAD (stale PID file — PID $PID not found)"
    fi
else
    echo "Process:     NOT RUNNING (no PID file)"
fi

if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    LISTENER_PID=$(lsof -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
    echo "Port $PORT:    LISTENING (PID $LISTENER_PID)"
else
    echo "Port $PORT:    NOT LISTENING"
fi

TUNNEL_STATUS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$TUNNEL_URL" 2>/dev/null || echo "000")
if [ "$TUNNEL_STATUS" = "200" ]; then
    echo "Tunnel:      OK ($TUNNEL_URL)"
elif [ "$TUNNEL_STATUS" = "000" ]; then
    echo "Tunnel:      UNREACHABLE ($TUNNEL_URL)"
else
    echo "Tunnel:      HTTP $TUNNEL_STATUS ($TUNNEL_URL)"
fi

if [ -d "$ROOT/frontend/dist" ]; then
    echo "Frontend:    BUILT (frontend/dist/ exists)"
else
    echo "Frontend:    MISSING (run: scripts/start.sh --rebuild)"
fi

if [ -f "$ROOT/.env" ]; then
    echo "Env file:    PRESENT"
else
    echo "Env file:    MISSING (.env not found)"
fi

echo ""

if [ -f "$LOG_FILE" ]; then
    echo "=== Last 10 log lines ==="
    tail -10 "$LOG_FILE"
else
    echo "(no log file at $LOG_FILE)"
fi
