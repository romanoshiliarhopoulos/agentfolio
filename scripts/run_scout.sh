#!/usr/bin/env bash
# run_scout.sh — Daily scout (Mon–Sat, 06:00)
# Runs precompute then the daily scout agent.
# Triggered by launchd: com.agentfolio.scout.plist
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/scout_$(date +%Y-%m-%d).log"

exec >> "$LOG" 2>&1

echo "========================================"
echo "scout  started: $(date)"
echo "========================================"

# Prevent system sleep for the duration of this script
caffeinate -i -w $$ &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

cd "$REPO"

echo "[1/2] Running precompute..."
"$PYTHON" src/precompute.py

echo "[2/2] Running scout agent..."
"$PYTHON" src/agents/scout.py

echo "scout  finished: $(date)"
