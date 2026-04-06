#!/usr/bin/env bash
# run_agent1.sh — Portfolio Analyzer (Sunday 02:00, Week A only)
# Runs precompute + agent1. Agents 2–5 use the same snapshot produced here.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly_$(date +%Y-W%V).log"

exec >> "$LOG" 2>&1

# ── Week A check (even ISO week = Week A) ─────────────────────────────────────
WEEK_NUM=$(date +%V | sed 's/^0*//')
if [ $(( WEEK_NUM % 2 )) -ne 0 ]; then
    echo "[agent1] Week $WEEK_NUM is Week B — skipping."
    exit 0
fi

echo "========================================"
echo "agent1 started: $(date)  [Week A, W$WEEK_NUM]"
echo "========================================"

caffeinate -i -w $$ &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

cd "$REPO"

echo "[1/2] Running precompute..."
"$PYTHON" src/precompute.py

echo "[2/2] Running agent1 (portfolio analyzer)..."
"$PYTHON" src/agents/agent1.py

echo "agent1 finished: $(date)"
