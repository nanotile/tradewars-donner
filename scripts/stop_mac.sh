#!/usr/bin/env bash
# Stop and remove the Tradewars container.

set -euo pipefail

if docker ps -a --format '{{.Names}}' | grep -q '^tradewars$'; then
  docker rm -f tradewars > /dev/null
  echo "tradewars stopped."
else
  echo "(no running tradewars container)"
fi
