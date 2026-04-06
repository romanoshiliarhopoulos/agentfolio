#!/usr/bin/env bash
# run_pulse.sh — Mid-cycle Pulse Check (Wednesday 06:00, Week B only)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pulse_$(date +%Y-W%V).log"

exec >> "$LOG" 2>&1

# ── Week B check (odd ISO week = Week B) ──────────────────────────────────────
WEEK_NUM=$(date +%V | sed 's/^0*//')
if [ $(( WEEK_NUM % 2 )) -eq 0 ]; then
    echo "[pulse] Week $WEEK_NUM is Week A — skipping."
    exit 0
fi

echo "========================================"
echo "pulse  started: $(date)  [Week B, W$WEEK_NUM]"
echo "========================================"

caffeinate -i -w $$ &
CAFF_PID=$!
trap "kill $CAFF_PID 2>/dev/null || true" EXIT

cd "$REPO"

echo "[1/2] Running precompute..."
"$PYTHON" src/precompute.py

echo "[2/2] Running pulse check..."
"$PYTHON" src/agents/agent_pulse.py

echo "pulse  finished: $(date)"
