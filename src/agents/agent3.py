"""
Agent 3 — Market Researcher

Deep opportunity discovery and macro assessment.
Runs Sunday Week A at ~13:00 (after Agents 1 and 2 complete).

Reads:
  data/context/market_research.json
  data/weekly/agent1_analysis.json
  data/weekly/agent2_risk.json
  data/scout_logs/  (high-signal logs from this week)
  data/reports/last_week_agent3.json

Writes:
  data/weekly/agent3_research.json
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, load_text, run_claude, extract_json, write_json,
    current_week, high_signal_scout_logs, concat_scout_logs, log, load_prompt,
)
from agents.news_filter import run as run_news_filter

AGENT = "agent3"
MAX_TURNS = 35

SYSTEM_PROMPT = load_prompt("agent3")


def build_context(research: dict, agent1: dict, agent2: dict,
                  scout_logs: str, last_week: dict, news_digest: dict) -> str:
    parts = []
    week = current_week()
    parts.append(f"# Market Research Context — {week}\n")

    # Sector performance
    parts.append("## Sector ETF Performance")
    for sym, d in research.get("sector_etfs", {}).items():
        chg = d.get("change_1d_pct")
        chg_5d = d.get("change_5d_pct")
        if chg is not None:
            parts.append(f"- {sym}: 1d={chg:+.1f}%  5d={chg_5d:+.1f}% (if available)")

    # News digest (Haiku-filtered) — preferred over raw headlines
    if news_digest and news_digest.get("top_headlines"):
        top = news_digest["top_headlines"]
        parts.append(f"\n## News Digest — Filtered & Scored ({len(top)} high-signal items)")
        macro_signal = news_digest.get("macro_signal", "")
        if macro_signal:
            parts.append(f"*Macro tone: {macro_signal}*")
        for h in top:
            sym_tag = f" [{h.get('symbol')}]" if h.get("symbol") else ""
            parts.append(f"- [{h.get('tag', '?')}]{sym_tag} {h['title']}  →  {h.get('why', '')}")
        themes = news_digest.get("key_themes", [])
        if themes:
            parts.append(f"\n  Key themes: {', '.join(themes)}")
        alerts = news_digest.get("holding_alerts", {})
        holding_alerts = {k: v for k, v in alerts.items() if v}
        if holding_alerts:
            parts.append("\n  Holding-specific signals:")
            for sym, note in holding_alerts.items():
                parts.append(f"    {sym}: {note}")
    else:
        # Fallback: raw headlines when digest not available
        headlines = research.get("headlines", [])
        parts.append(f"\n## News Headlines ({len(headlines)} items)")
        for h in headlines[:25]:
            parts.append(f"- [{h['source']}] {h['title']}")

    # Events calendar
    events = research.get("events_calendar", [])
    parts.append(f"\n## Events Calendar (next 14 days, {len(events)} events)")
    for e in events:
        parts.append(f"- {e['date']} ({e['days_until']}d): {e['type']} — "
                     f"{e.get('symbol', 'macro')}")

    # Analyst data — price targets and consensus ratings for stock holdings
    analyst_data = research.get("analyst_data", {})
    if analyst_data:
        parts.append("\n## Analyst Consensus (stock holdings only)")
        for sym, data in analyst_data.items():
            pt = data.get("price_targets", {})
            rec = data.get("recommendations", {})
            upside = pt.get("upside_pct")
            mean_t = pt.get("mean")
            upside_str = f"  upside to mean target: {upside:+.1f}%" if upside is not None else ""
            mean_str   = f"  mean target: ${mean_t:.2f}" if mean_t else ""
            strongbuy  = rec.get("strongBuy", 0)
            buy        = rec.get("buy", 0)
            hold       = rec.get("hold", 0)
            sell       = rec.get("sell", 0) + rec.get("strongSell", 0)
            parts.append(f"  {sym}: Buy={strongbuy+buy}  Hold={hold}  Sell={sell}{mean_str}{upside_str}")

    # Agent 1 summary (allocation gaps are relevant for candidate selection)
    parts.append("\n## Agent 1 — Portfolio Allocation Analysis")
    if agent1:
        alloc = agent1.get("allocation", {})
        parts.append(f"  Geographic concentration: {json.dumps(alloc.get('by_geography', {}))}")
        parts.append(f"  Sector concentration: {json.dumps(alloc.get('by_sector', {}))}")
        drift = agent1.get("drift_summary", {})
        drift_str = drift if isinstance(drift, str) else drift.get("summary", "n/a")
        parts.append(f"  Allocation drift: {drift_str}")
        gaps = alloc.get("gaps_and_overlaps", [])
        if gaps:
            parts.append("  Gaps/overlaps identified by Agent 1:")
            for g in gaps:
                parts.append(f"    - {g}")
    else:
        parts.append("  (Agent 1 output not available)")

    # Agent 2 key findings
    parts.append("\n## Agent 2 — Risk Assessment Summary")
    if agent2:
        parts.append(f"  Risk score: {agent2.get('risk_score', 'n/a')}/10  "
                     f"({agent2.get('risk_score_rationale', '')})")
        parts.append(f"  Concentration: {agent2.get('concentration_risk', {}).get('level', 'n/a')} — "
                     f"{agent2.get('concentration_risk', {}).get('detail', '')}")
        key_risks = agent2.get("key_risks", [])
        if key_risks:
            parts.append("  Key risks:")
            for r in key_risks:
                parts.append(f"    - {r}")
    else:
        parts.append("  (Agent 2 output not available)")

    # High-signal scout logs
    parts.append("\n## High-Signal Scout Logs (this week)")
    parts.append(scout_logs)

    # Prior candidates
    if last_week:
        parts.append("\n## Last Week's Research Output (for candidate tracking)")
        prior_candidates = last_week.get("opportunity_candidates", [])
        if prior_candidates:
            parts.append("  Prior opportunity candidates:")
            for c in prior_candidates:
                parts.append(f"  - {c.get('theme')} ({c.get('ucits_instrument')}): "
                             f"conviction={c.get('conviction')}  fit={c.get('fit_for_portfolio')}")
        prior_macro = last_week.get("macro_assessment", {})
        if prior_macro:
            parts.append(f"  Prior macro regime: {prior_macro.get('regime')} — "
                         f"{prior_macro.get('regime_rationale', '')}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting market research")

    research  = load_json(DATA / "context" / "market_research.json")
    agent1    = load_json(DATA / "weekly" / "agent1_analysis.json")
    agent2    = load_json(DATA / "weekly" / "agent2_risk.json")
    last_week = load_json(DATA / "reports" / "last_week_agent3.json")

    if not research:
        log(AGENT, "WARNING: market_research.json missing — research context will be thin")

    scout_logs = concat_scout_logs(high_signal_scout_logs())

    # Run Haiku news filter — scores and clusters multi-day headlines cheaply
    news_digest = run_news_filter()

    context = build_context(research, agent1, agent2, scout_logs, last_week, news_digest)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get('AGENTFOLIO_MAX_TURNS', MAX_TURNS))})...")

    raw = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)
    result = extract_json(raw)
    result["_generated_at"] = __import__("datetime").datetime.now().isoformat()

    out_path = DATA / "weekly" / "agent3_research.json"
    write_json(out_path, result)
    log(AGENT, f"Wrote {out_path}")

    n_candidates = len(result.get("opportunity_candidates", []))
    regime = result.get("macro_assessment", {}).get("regime", "?")
    log(AGENT, f"Regime: {regime}  |  Candidates: {n_candidates}")


if __name__ == "__main__":
    run()
