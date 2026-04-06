import os
"""
Agent Pulse — Mid-cycle Check (Week B)

Runs Wednesday of Week B (mid-cycle week) to catch anything that has drifted
materially since the Sunday Week A deep analysis.

This is a lightweight session — not a full re-analysis. It only escalates if
something genuinely warrants attention before next Sunday's full run.

Reads:
  data/context/portfolio_snapshot.json
  data/weekly/agent4_strategy.json          (Week A strategy)
  data/reports/YYYY-WNN-report.md           (Week A final report)
  data/scout_logs/  (all logs since Week A Sunday)

Writes:
  data/weekly/pulse_check.json
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, load_text,
    run_claude, extract_json, write_json,
    current_week, scout_logs_this_week, concat_scout_logs, log, load_prompt,
)

AGENT = "pulse"
MAX_TURNS = 40

SYSTEM_PROMPT = load_prompt("pulse")


def _find_last_week_a_report() -> str:
    """Find the most recent Week A final report markdown file."""
    reports_dir = DATA / "reports"
    if not reports_dir.exists():
        return ""
    reports = sorted(reports_dir.glob("*-W*-report.md"))
    if not reports:
        return ""
    return reports[-1].read_text()


def build_context(snapshot: dict, agent4_strategy: dict, scout_logs: str,
                  week_a_report: str) -> str:
    parts = []
    week = current_week()
    today = datetime.date.today().isoformat()
    parts.append(f"# Pulse Check Context — {week} ({today})\n")

    # Current NAV and holdings
    nav = snapshot.get("nav", {})
    parts.append(f"## Current Portfolio NAV\n"
                 f"Total: ${nav.get('total', 0):,.2f}  |  "
                 f"Invested: ${nav.get('stock', 0):,.2f}  |  "
                 f"Cash: ${nav.get('cash', 0):,.2f}")

    parts.append("\n## Current Holdings")
    for sym, h in snapshot.get("holdings", {}).items():
        pnl = h.get("unrealized_pnl_pct", 0)
        val = h.get("position_value", 0)
        chg = h.get("change_1d_pct")
        chg_str = f"{chg:+.1f}%" if chg is not None else "n/a"
        parts.append(f"  {sym:6}  ${val:>8,.2f}  PnL: {pnl:+.1f}%  1d: {chg_str}")

    # Current market indicators
    s = snapshot.get("sentiment", {})
    m = snapshot.get("macro", {})
    parts.append(f"\n## Current Market Indicators")
    parts.append(f"  VIX: {s.get('vix')} ({s.get('vix_regime')})  5d change: {s.get('vix_change_5d')}")
    parts.append(f"  EUR/USD: {s.get('eurusd')}  {s.get('eurusd_note', '')}")
    parts.append(f"  HY credit spread: {m.get('hy_spread')}%")
    parts.append(f"  10y-2y yield spread: {m.get('yield_curve_spread')}%")

    # Holding flags (may signal threshold breaches)
    flags = snapshot.get("holding_flags", [])
    if flags:
        parts.append("\n## Holding Flags")
        for f in flags:
            parts.append(f"  [{f['flag']}] {f['symbol']}: {f['reason']}")

    # Week A strategy (the plan to compare against)
    if agent4_strategy:
        parts.append("\n## Week A Strategy Recommendations (the plan)")
        recs = agent4_strategy.get("recommendations", [])
        for r in recs:
            parts.append(f"  [{r.get('urgency')}] P{r.get('priority')}: {r.get('action')}")
            parts.append(f"    Rationale: {r.get('rationale', '')}")
        cash = agent4_strategy.get("cash_deployment", {})
        if cash:
            parts.append(f"  Cash deployment: {cash.get('strategy')} — {cash.get('rationale', '')}")
        outlook = agent4_strategy.get("multi_week_outlook", [])
        if outlook:
            parts.append("  Multi-week themes to watch:")
            for o in outlook:
                parts.append(f"    {o.get('theme')}: {o.get('watch_for')}")
    else:
        parts.append("\n## Week A Strategy\n  (No Week A strategy found — first run?)")

    # Scout logs since Week A Sunday
    parts.append("\n## Scout Logs Since Week A")
    parts.append(scout_logs)

    # Week A final report (for baseline comparison)
    if week_a_report:
        parts.append("\n## Week A Final Report (baseline)")
        # Include only the first ~2000 chars to avoid bloating context
        parts.append(week_a_report[:2000])
        if len(week_a_report) > 2000:
            parts.append("... [truncated — see full report in data/reports/]")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting mid-cycle pulse check")

    snapshot       = load_json(DATA / "context" / "portfolio_snapshot.json")
    agent4_strategy = load_json(DATA / "weekly" / "agent4_strategy.json")
    week_a_report  = _find_last_week_a_report()
    all_scout_logs = concat_scout_logs(scout_logs_this_week())

    if not snapshot:
        log(AGENT, "ERROR: portfolio_snapshot.json missing")
        sys.exit(1)

    context = build_context(snapshot, agent4_strategy, all_scout_logs, week_a_report)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get("AGENTFOLIO_MAX_TURNS", MAX_TURNS))})...")

    raw = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)
    result = extract_json(raw)
    result["_generated_at"] = __import__("datetime").datetime.now().isoformat()

    out_path = DATA / "weekly" / "pulse_check.json"
    write_json(out_path, result)
    log(AGENT, f"Wrote {out_path}")

    verdict = result.get("verdict", "?")
    escalations = result.get("escalations", [])
    n_esc = len(escalations)
    log(AGENT, f"Verdict: {verdict}  |  Escalations: {n_esc}")

    if verdict == "ESCALATE" and escalations:
        log(AGENT, "=== ESCALATION ITEMS ===")
        for e in escalations:
            log(AGENT, f"  [{e.get('severity')}] {e.get('category')}: {e.get('description')}")


if __name__ == "__main__":
    run()
