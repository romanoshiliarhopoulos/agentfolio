import os
"""
Agent 2 — Risk Assessor

Multi-scenario risk analysis building on Agent 1's allocation picture.
Runs Sunday Week A at 07:00 AM.

Reads:
  data/context/portfolio_snapshot.json
  data/weekly/agent1_analysis.json
  data/reports/last_week_agent2.json

Writes:
  data/weekly/agent2_risk.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, run_claude, extract_json, write_json,
    current_week, log, load_prompt,
)

AGENT = "agent2"
MAX_TURNS = 40

SYSTEM_PROMPT = load_prompt("agent2")


def build_context(snapshot: dict, agent1: dict, last_week: dict) -> str:
    parts = []
    week = current_week()
    parts.append(f"# Risk Assessment Context — {week}\n")

    nav = snapshot.get("nav", {})
    parts.append(f"## Portfolio NAV\n"
                 f"Total: ${nav.get('total', 0):,.2f}  |  "
                 f"Invested: ${nav.get('stock', 0):,.2f}  |  "
                 f"Cash: ${nav.get('cash', 0):,.2f}")

    parts.append("\n## Holdings (for stress calculations)")
    for sym, h in snapshot.get("holdings", {}).items():
        parts.append(f"  {sym:6}  ${h.get('position_value', 0):>8,.2f}  "
                     f"({h.get('position_value', 0) / max(nav.get('total', 1), 1) * 100:.1f}% of NAV)  "
                     f"sector: {h.get('sector', 'n/a')}")

    parts.append("\n## Pre-computed Risk Proxies")
    risk = snapshot.get("risk", {})
    parts.append(f"  Portfolio beta (approx): {risk.get('portfolio_beta_approx', 'n/a')}")
    parts.append(f"  Largest single position: {risk.get('concentration_top1_pct', 'n/a'):.1f}%")
    parts.append("  Pre-computed stress scenarios:")
    for s in risk.get("stress_scenarios", []):
        parts.append(f"    {s['scenario']}: ${s['estimated_impact_usd']:+,.0f} "
                     f"({s['impact_pct_of_nav']:+.1f}% of NAV) → "
                     f"new NAV ~${s['estimated_new_nav']:,.0f}")

    parts.append("\n## Market Sentiment & Macro")
    s = snapshot.get("sentiment", {})
    m = snapshot.get("macro", {})
    parts.append(f"  VIX: {s.get('vix')} ({s.get('vix_regime')})  "
                 f"5d: {s.get('vix_change_5d'):+.2f}")
    parts.append(f"  EUR/USD: {s.get('eurusd')}  1mo: {s.get('eurusd_change_1mo_pct'):+.1f}%")
    parts.append(f"  {s.get('eurusd_note', '')}")
    parts.append(f"  HY credit spread: {m.get('hy_spread')}%  "
                 f"(elevated = credit stress, >6% = crisis territory)")
    parts.append(f"  10y-2y yield spread: {m.get('yield_curve_spread')}%  "
                 f"(negative = inverted curve, recession signal)")
    parts.append(f"  S&P 500 P/E: {s.get('sp500_trailing_pe')}  "
                 f"— {s.get('sp500_trailing_pe_note', '')}")

    parts.append("\n## Agent 1 — Portfolio Analysis Output")
    parts.append(json.dumps(agent1, indent=2))

    if last_week:
        parts.append("\n## Last Week's Risk Assessment (for trend comparison)")
        parts.append(json.dumps(last_week, indent=2))

    chronicle = snapshot.get("market_chronicle", "")
    if chronicle and "No historical" not in chronicle:
        parts.append(f"\n{chronicle}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting risk assessment")

    snapshot = load_json(DATA / "context" / "portfolio_snapshot.json")
    agent1   = load_json(DATA / "weekly" / "agent1_analysis.json")
    last_week = load_json(DATA / "reports" / "last_week_agent2.json")

    if not snapshot:
        log(AGENT, "ERROR: portfolio_snapshot.json missing")
        sys.exit(1)
    if not agent1:
        log(AGENT, "ERROR: agent1_analysis.json missing — run agent1 first")
        sys.exit(1)

    context = build_context(snapshot, agent1, last_week)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get("AGENTFOLIO_MAX_TURNS", MAX_TURNS))})...")

    raw = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)
    result = extract_json(raw)
    result["_generated_at"] = __import__("datetime").datetime.now().isoformat()

    out_path = DATA / "weekly" / "agent2_risk.json"
    write_json(out_path, result)
    log(AGENT, f"Wrote {out_path}")


if __name__ == "__main__":
    run()
