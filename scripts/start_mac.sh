#!/usr/bin/env bash
# Build and start the Tradewars container on macOS.
#
# Reads API keys from .env at the repo root and passes them through to the
# container via --env-file. Doesn't bake secrets into the image.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "missing .env at $ROOT — needs MASSIVE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, DEEPSEEK_API_KEY, OPENROUTER_API_KEY" >&2
  exit 1
fi

if ! docker info > /dev/null 2>&1; then
  echo "starting Docker Desktop..."
  open -a Docker
  for _ in $(seq 1 30); do
    docker info > /dev/null 2>&1 && break
    sleep 2
  done
fi

echo "building tradewars image..."
docker build -t tradewars .

echo "stopping any previous container..."
docker rm -f tradewars > /dev/null 2>&1 || true

echo "starting tradewars on http://localhost:8000 ..."
docker run -d --name tradewars \
  -p 8000:8000 \
  --env-file .env \
  tradewars

echo
echo "tradewars is up. open http://localhost:8000"
echo "logs:  docker logs -f tradewars"
echo "stop:  scripts/stop_mac.sh"
