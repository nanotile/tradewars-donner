#!/usr/bin/env bash
# Module: start.sh, Version 1.0.0, Iteration v1
# Developer: Kent Benson
# UV Environment: uv run (auto-managed)
#
# Start the Tradewars backend on this Linux VM.
# Runs uvicorn in the background, tracks PID, logs output.
#
# Usage:
#   scripts/start.sh              Start on default port 5060
#   scripts/start.sh --port 5070  Start on a custom port
#   scripts/start.sh --rebuild    Rebuild frontend before starting

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPTS_DIR/.tradewars.pid"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/tradewars.log"
DEFAULT_PORT=5060

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1"
}

check_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

validate_env() {
    local env_file="$ROOT/.env"
    if [ ! -f "$env_file" ]; then
        log "ERROR: missing .env at $ROOT"
        log "Needs: MASSIVE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, DEEPSEEK_API_KEY"
        exit 1
    fi

    local missing=()
    for key in MASSIVE_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY; do
        local val
        val=$(grep "^${key}=" "$env_file" | cut -d'=' -f2- | tr -d '[:space:]')
        if [ -z "$val" ]; then
            missing+=("$key")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        log "ERROR: empty or missing keys in .env: ${missing[*]}"
        exit 1
    fi
}

validate_frontend() {
    if [ ! -d "$ROOT/frontend/dist" ]; then
        log "WARNING: frontend/dist/ not found — UI won't load"
        log "Run: scripts/start.sh --rebuild"
        return 1
    fi
    return 0
}

rebuild_frontend() {
    log "rebuilding frontend..."
    cd "$ROOT/frontend"
    npm run build
    cd "$ROOT"
    log "frontend build complete"
}

PORT=$DEFAULT_PORT
DO_REBUILD=false

while [ $# -gt 0 ]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --rebuild)
            DO_REBUILD=true
            shift
            ;;
        *)
            echo "Usage: $0 [--port <N>] [--rebuild]"
            exit 1
            ;;
    esac
done

if existing_pid=$(check_running); then
    log "tradewars is already running (PID $existing_pid)"
    log "stop it first: scripts/stop.sh"
    exit 1
fi

if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    local_pid=$(lsof -i ":$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
    log "ERROR: port $PORT already in use by PID $local_pid"
    exit 1
fi

validate_env

if [ "$DO_REBUILD" = true ]; then
    rebuild_frontend
else
    validate_frontend || true
fi

mkdir -p "$LOG_DIR"

log "starting tradewars on http://127.0.0.1:$PORT ..."
cd "$ROOT"
nohup uv run uvicorn --factory backend.api.app:create_app \
    --host 127.0.0.1 \
    --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
log "PID $(cat "$PID_FILE") written to $PID_FILE"

for i in $(seq 1 10); do
    if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
        log "tradewars is up on http://127.0.0.1:$PORT"
        echo ""
        echo "  tunnel:  https://tradewars.kentbenson.net"
        echo "  logs:    tail -f $LOG_FILE"
        echo "  stop:    scripts/stop.sh"
        echo "  status:  scripts/status.sh"
        exit 0
    fi
    sleep 0.5
done

log "WARNING: port $PORT not listening after 5 seconds — check logs"
log "  tail -20 $LOG_FILE"
exit 1
