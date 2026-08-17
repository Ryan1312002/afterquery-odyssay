#!/usr/bin/env bash
set -u
mkdir -p "${BONDED_REWARD_DIR:-/logs/verifier}"
export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
python3 /tests/grade.py
exit 0
