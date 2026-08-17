#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
rm -rf /app/bonded
cp -a "$ROOT/bonded" /app/bonded
find /app/bonded -type d -name '__pycache__' -prune -exec rm -rf {} +
# Editable install already points at /app; refresh the console script just in case.
python3 -m pip install --no-index --offline -e /app >/dev/null 2>&1 || true
