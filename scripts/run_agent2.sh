#!/usr/bin/env bash
# run_agent2.sh — Risk Assessor (Sunday 07:15, Week A only)
# Reads agent1_analysis.json produced earlier this morning.
# No precompute — uses the snapshot written by run_agent1.sh.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly_$(date +%Y-W%V).log"

exec >> "$LOG" 2>&1

WEEK_NUM=$(date +%V | sed 's/^0*//')
if [ $(( WEEK_NUM % 2 )) -ne 0 ]; then
    echo "[agent2] Week $WEEK_NUM is Week B — skipping."
    exit 0
fi

# Guard: agent1 output must exist and be from today
AGENT1_OUT="$REPO/data/weekly/agent1_analysis.json"
if [ ! -f "$AGENT1_OUT" ]; then
    echo "[agent2] ERROR: agent1_analysis.json not found. Did agent1 run?"
    exit 1
fi

echo "========================================"
echo "agent2 started: $(date)  [Week A, W$WEEK_NUM]"
echo "========================================"

caffeinate -i -w $$ &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

cd "$REPO"
"$PYTHON" src/agents/agent2.py

echo "agent2 finished: $(date)"
