import os
"""
Agent 5 — Report Generator

Synthesises all agent outputs into the final human-readable weekly report.
Runs Sunday Week A at ~22:00 (after all other agents complete).

Reads:
  data/weekly/agent1_analysis.json
  data/weekly/agent2_risk.json
  data/weekly/agent3_research.json
  data/weekly/agent4_strategy.json
  data/context/portfolio_snapshot.json
  config/investor_profile.yaml

Writes:
  data/reports/YYYY-WNN-report.md
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, load_investor_profile,
    run_claude, write_text,
    current_week, log, load_prompt,
)

AGENT = "agent5"
MAX_TURNS = 28

SYSTEM_PROMPT = load_prompt("agent5")


def build_context(snapshot: dict, agent1: dict, agent2: dict,
                  agent3: dict, agent4: dict, investor_profile: str) -> str:
    """Build the synthesis context for the report generator."""
    import json
    parts = []
    week = current_week()
    today = datetime.date.today().isoformat()
    parts.append(f"# Report Generation Context — {week} ({today})\n")

    # Investor profile (brief)
    parts.append("## Investor Profile")
    parts.append(investor_profile)

    # NAV
    nav = snapshot.get("nav", {})
    parts.append(f"\n## Portfolio Summary\n"
                 f"NAV: ${nav.get('total', 0):,.2f}  |  "
                 f"Invested: ${nav.get('stock', 0):,.2f}  |  "
                 f"Cash: ${nav.get('cash', 0):,.2f}")

    # Holdings table data
    parts.append("\n## Holdings Detail")
    for sym, h in snapshot.get("holdings", {}).items():
        pnl = h.get("unrealized_pnl_pct", 0)
        val = h.get("position_value", 0)
        weight = val / max(nav.get("total", 1), 1) * 100
        chg = h.get("change_1d_pct")
        chg_str = f"{chg:+.1f}%" if chg is not None else "n/a"
        parts.append(f"  {sym}: ${val:,.2f}  weight={weight:.1f}%  pnl={pnl:+.1f}%  1d={chg_str}")

    # Holding flags
    flags = snapshot.get("holding_flags", [])
    if flags:
        parts.append("\n## Holding Flags")
        for f in flags:
            parts.append(f"  [{f['flag']}] {f['symbol']}: {f['reason']}")

    # Market data
    s = snapshot.get("sentiment", {})
    m = snapshot.get("macro", {})
    parts.append(f"\n## Market Data")
    parts.append(f"  VIX: {s.get('vix')} ({s.get('vix_regime')})  "
                 f"EUR/USD: {s.get('eurusd')} ({s.get('eurusd_note', '')})")
    parts.append(f"  10y: {m.get('t10y')}%  2y: {m.get('t2y')}%  "
                 f"spread: {m.get('yield_curve_spread')}%")
    parts.append(f"  Fed: {m.get('fed_funds')}%  CPI: {m.get('cpi_yoy')}%  "
                 f"Unemp: {m.get('unemployment')}%  HY: {m.get('hy_spread')}%  "
                 f"S&P PE: {s.get('sp500_trailing_pe')}")

    # All four agent outputs
    parts.append("\n## Agent 1 — Portfolio Analysis")
    parts.append(json.dumps(agent1, indent=2))

    parts.append("\n## Agent 2 — Risk Assessment")
    parts.append(json.dumps(agent2, indent=2))

    parts.append("\n## Agent 3 — Market Research")
    parts.append(json.dumps(agent3, indent=2))

    parts.append("\n## Agent 4 — Strategy Recommendations")
    parts.append(json.dumps(agent4, indent=2))

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting report generation")

    snapshot = load_json(DATA / "context" / "portfolio_snapshot.json")
    agent1   = load_json(DATA / "weekly" / "agent1_analysis.json")
    agent2   = load_json(DATA / "weekly" / "agent2_risk.json")
    agent3   = load_json(DATA / "weekly" / "agent3_research.json")
    agent4   = load_json(DATA / "weekly" / "agent4_strategy.json")
    investor_profile = load_investor_profile()

    if not snapshot:
        log(AGENT, "ERROR: portfolio_snapshot.json missing")
        sys.exit(1)
    if not agent4:
        log(AGENT, "WARNING: agent4_strategy.json missing — report will be incomplete")

    context = build_context(snapshot, agent1, agent2, agent3, agent4, investor_profile)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get("AGENTFOLIO_MAX_TURNS", MAX_TURNS))})...")

    output = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)

    # Write report
    week = current_week()
    out_path = DATA / "reports" / f"{week}-report.md"
    write_text(out_path, output)
    log(AGENT, f"Wrote {out_path}")

    # Archive current weekly JSONs as "last week" for next run's continuity
    _archive_weekly_outputs(week)

    # Print section headers as quick status
    headers = [l for l in output.split("\n") if l.startswith("###")]
    log(AGENT, f"Report sections: {len(headers)}")


def _archive_weekly_outputs(week: str) -> None:
    """
    Copy this week's agent outputs to data/reports/last_week_agentN.json
    so next week's agents have prior context.
    """
    import shutil
    mappings = {
        DATA / "weekly" / "agent1_analysis.json": DATA / "reports" / "last_week_agent1.json",
        DATA / "weekly" / "agent2_risk.json":     DATA / "reports" / "last_week_agent2.json",
        DATA / "weekly" / "agent3_research.json": DATA / "reports" / "last_week_agent3.json",
        DATA / "weekly" / "agent4_strategy.json": DATA / "reports" / "last_week_agent4.json",
    }
    archived = 0
    for src, dst in mappings.items():
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            archived += 1
    log(AGENT, f"Archived {archived} weekly outputs to data/reports/last_week_*.json")


if __name__ == "__main__":
    run()
