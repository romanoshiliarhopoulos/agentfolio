import os
"""
Agent 4 — Strategy Advisor

Synthesises all prior agents into actionable portfolio guidance.
Runs Sunday Week A at ~18:00 (after Agents 1, 2, 3 complete).

Reads:
  data/context/portfolio_snapshot.json
  data/weekly/agent1_analysis.json
  data/weekly/agent2_risk.json
  data/weekly/agent3_research.json
  data/reports/last_week_agent4.json
  config/investor_profile.yaml

Writes:
  data/weekly/agent4_strategy.json
  (also appends chronicle_entry to data/chronicle/market_chronicle.json)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, load_text, load_investor_profile,
    run_claude, extract_json, write_json,
    current_week, log, load_prompt,
)
import chronicle

AGENT = "agent4"
MAX_TURNS = 43

SYSTEM_PROMPT = load_prompt("agent4")


def build_context(snapshot: dict, agent1: dict, agent2: dict,
                  agent3: dict, last_week: dict, investor_profile: str) -> str:
    parts = []
    week = current_week()
    parts.append(f"# Strategy Synthesis Context — {week}\n")

    # Investor profile
    parts.append("## Investor Profile")
    parts.append(investor_profile)

    # Portfolio NAV
    nav = snapshot.get("nav", {})
    parts.append(f"\n## Portfolio NAV\n"
                 f"Total: ${nav.get('total', 0):,.2f}  |  "
                 f"Invested: ${nav.get('stock', 0):,.2f}  |  "
                 f"Cash: ${nav.get('cash', 0):,.2f}  |  "
                 f"Cash %: {nav.get('cash', 0) / max(nav.get('total', 1), 1) * 100:.1f}%")

    # Holdings summary
    parts.append("\n## Current Holdings")
    for sym, h in snapshot.get("holdings", {}).items():
        pnl = h.get("unrealized_pnl_pct", 0)
        val = h.get("position_value", 0)
        weight = val / max(nav.get("total", 1), 1) * 100
        parts.append(f"  {sym:6}  ${val:>8,.2f}  ({weight:.1f}% NAV)  "
                     f"PnL: {pnl:+.1f}%  sector: {h.get('sector', 'n/a')}")

    # Macro and sentiment
    s = snapshot.get("sentiment", {})
    m = snapshot.get("macro", {})
    parts.append(f"\n## Market Environment")
    parts.append(f"  VIX: {s.get('vix')} ({s.get('vix_regime')})  5d: {s.get('vix_change_5d')}")
    parts.append(f"  EUR/USD: {s.get('eurusd')}  1mo: {s.get('eurusd_change_1mo_pct', 0):+.1f}%  "
                 f"{s.get('eurusd_note', '')}")
    parts.append(f"  10y yield: {m.get('t10y')}%  |  2y yield: {m.get('t2y')}%  "
                 f"|  Spread: {m.get('yield_curve_spread')}%")
    parts.append(f"  Fed Funds: {m.get('fed_funds')}%  |  CPI YoY: {m.get('cpi_yoy')}%  "
                 f"|  Unemployment: {m.get('unemployment')}%")
    parts.append(f"  HY Spread: {m.get('hy_spread')}%  |  S&P 500 P/E: {s.get('sp500_trailing_pe')}")

    # Agent 1 — full output
    parts.append("\n## Agent 1 — Portfolio Analysis")
    parts.append(json.dumps(agent1, indent=2))

    # Agent 2 — full output
    parts.append("\n## Agent 2 — Risk Assessment")
    parts.append(json.dumps(agent2, indent=2))

    # Agent 3 — full output
    parts.append("\n## Agent 3 — Market Research")
    parts.append(json.dumps(agent3, indent=2))

    # Prior week strategy (for continuity)
    if last_week:
        parts.append("\n## Last Week's Strategy Output (for continuity)")
        # Summarise the key decisions without dumping the whole thing
        recs = last_week.get("recommendations", [])
        if recs:
            parts.append("  Prior recommendations:")
            for r in recs:
                parts.append(f"    [{r.get('urgency')}] {r.get('action')}")
        verdicts = last_week.get("research_candidate_verdicts", [])
        if verdicts:
            parts.append("  Prior research verdicts:")
            for v in verdicts:
                parts.append(f"    {v.get('verdict')} — {v.get('theme')} ({v.get('ucits_instrument')})")
        outlook = last_week.get("multi_week_outlook", [])
        if outlook:
            parts.append("  Prior multi-week themes:")
            for o in outlook:
                parts.append(f"    {o.get('theme')}: watch for {o.get('watch_for')}")

    # Market chronicle
    chronicle_text = chronicle.summarise_for_context(weeks=12)
    if "No historical" not in chronicle_text:
        parts.append(f"\n{chronicle_text}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting strategy synthesis")

    snapshot  = load_json(DATA / "context" / "portfolio_snapshot.json")
    agent1    = load_json(DATA / "weekly" / "agent1_analysis.json")
    agent2    = load_json(DATA / "weekly" / "agent2_risk.json")
    agent3    = load_json(DATA / "weekly" / "agent3_research.json")
    last_week = load_json(DATA / "reports" / "last_week_agent4.json")
    investor_profile = load_investor_profile()

    if not snapshot:
        log(AGENT, "ERROR: portfolio_snapshot.json missing")
        sys.exit(1)
    if not agent1:
        log(AGENT, "ERROR: agent1_analysis.json missing — run agent1 first")
        sys.exit(1)

    context = build_context(snapshot, agent1, agent2, agent3, last_week, investor_profile)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get("AGENTFOLIO_MAX_TURNS", MAX_TURNS))})...")

    raw = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)
    result = extract_json(raw)
    result["_generated_at"] = __import__("datetime").datetime.now().isoformat()

    # Append chronicle entry if present and valid
    chronicle_entry = result.get("chronicle_entry", {})
    if chronicle_entry:
        chronicle.append_entry(chronicle_entry)
        log(AGENT, f"Appended chronicle entry for {chronicle_entry.get('week', '?')}")

    out_path = DATA / "weekly" / "agent4_strategy.json"
    write_json(out_path, result)
    log(AGENT, f"Wrote {out_path}")

    risk_posture = result.get("risk_posture", "?")
    n_recs = len(result.get("recommendations", []))
    log(AGENT, f"Risk posture: {risk_posture}  |  Recommendations: {n_recs}")


if __name__ == "__main__":
    run()
