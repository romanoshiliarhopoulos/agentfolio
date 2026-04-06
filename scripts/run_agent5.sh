#!/usr/bin/env bash
# run_agent5.sh — Report Generator (Sunday 23:00, Week A only)
# Also archives weekly outputs to last_week_*.json for next run's continuity.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly_$(date +%Y-W%V).log"

exec >> "$LOG" 2>&1

WEEK_NUM=$(date +%V | sed 's/^0*//')
if [ $(( WEEK_NUM % 2 )) -ne 0 ]; then
    echo "[agent5] Week $WEEK_NUM is Week B — skipping."
    exit 0
fi

echo "========================================"
echo "agent5 started: $(date)  [Week A, W$WEEK_NUM]"
echo "========================================"

caffeinate -i -w $$ &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

cd "$REPO"
"$PYTHON" src/agents/agent5.py

echo "agent5 finished: $(date)"
echo "======== WEEKLY PIPELINE COMPLETE ======"
