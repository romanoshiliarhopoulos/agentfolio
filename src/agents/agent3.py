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
    DATA, load_investor_profile, load_json, load_text, run_claude, extract_json, write_json,
    current_week, high_signal_scout_logs, concat_scout_logs, log, load_prompt,
)
from agents.news_filter import run as run_news_filter

AGENT = "agent3"
MAX_TURNS = 35

SYSTEM_PROMPT = load_prompt("agent3")


def build_context(research: dict, agent1: dict, agent2: dict,
                  scout_logs: str, prior_weeks: list, news_digest: dict) -> str:
    parts = []
    week = current_week()
    parts.append(f"# Market Research Context — {week}\n")

    investor_profile = load_investor_profile()
    parts.append(f"# Investor profile: \n{investor_profile}")

    # Market Chronicle — long-term macro regime memory (highest priority context)
    chronicle_text = research.get("market_chronicle", "")
    if chronicle_text:
        parts.append("## Market Chronicle (last 12 weeks — macro regime history)")
        parts.append(chronicle_text)

    # Macro indicators — expanded set including activity and sentiment
    macro = research.get("macro", {})
    if macro:
        parts.append("## Macro Indicators")
        def _fmt(v, fmt=".2f", suffix=""):
            return f"{v:{fmt}}{suffix}" if v is not None else "n/a"
        parts.append(
            f"  Yields: 10y={_fmt(macro.get('t10y'))}%  2y={_fmt(macro.get('t2y'))}%  "
            f"spread={_fmt(macro.get('yield_curve_spread'))}%  "
            f"fed_funds={_fmt(macro.get('fed_funds'))}%"
        )
        parts.append(
            f"  Inflation: CPI YoY={_fmt(macro.get('cpi_yoy'))}%  "
            f"PCE YoY={_fmt(macro.get('pce_yoy'))}%"
        )
        parts.append(
            f"  Inflation: CPI YoY={_fmt(macro.get('cpi_yoy'))}%  "
            f"Core CPI YoY={_fmt(macro.get('core_cpi'))}%  "
            f"PCE YoY={_fmt(macro.get('pce_yoy'))}%"
        )
        parts.append(
            f"  Activity: Industrial Prod={_fmt(macro.get('industrial_prod'), '.1f')}  "
            f"Retail Sales MoM={_fmt(macro.get('retail_sales_mom'))}%  "
            f"Unemployment={_fmt(macro.get('unemployment'))}%"
        )
        cli = macro.get("leading_indicator")
        cli_regime = macro.get("leading_indicator_regime", "")
        parts.append(
            f"  OECD Leading Indicator={_fmt(cli, '.2f')} ({cli_regime})  "
            f"Real GDP={_fmt(macro.get('real_gdp_growth'))}%  "
            f"Housing Starts={_fmt(macro.get('housing_starts'), '.0f', 'k')}"
        )
        ecb = macro.get("ecb_rate")
        consumer = macro.get("consumer_sent")
        if ecb is not None or consumer is not None:
            parts.append(
                f"  ECB rate={_fmt(ecb)}%  "
                f"Consumer Sentiment={_fmt(consumer, '.1f')}"
            )
        hy = macro.get("hy_spread")
        if hy is not None:
            parts.append(f"  HY Credit Spread={_fmt(hy)}% OAS  (>6% = stress, >8% = crisis)")

    # Fear & Greed — market psychology overlay
    fg = research.get("fear_greed", {})
    if fg and not fg.get("error"):
        score = fg.get("score")
        label = fg.get("label", "")
        s1w   = fg.get("score_1w_ago")
        l1w   = fg.get("label_1w_ago", "")
        s1mo  = fg.get("score_1mo_ago")
        l1mo  = fg.get("label_1mo_ago", "")
        score_str = f"{score:.0f}" if score is not None else "n/a"
        s1w_str   = f"{s1w:.0f} ({l1w})" if s1w is not None else "n/a"
        s1mo_str  = f"{s1mo:.0f} ({l1mo})" if s1mo is not None else "n/a"
        parts.append(
            f"\n## CNN Fear & Greed Index\n"
            f"  Now: {score_str} — {label}  |  1w ago: {s1w_str}  |  1mo ago: {s1mo_str}\n"
            f"  (0=Extreme Fear → 100=Extreme Greed; extreme readings are contrarian signals)"
        )

    # Sector performance — multi-timeframe for structural vs noise distinction
    parts.append("## Sector ETF Performance (multi-timeframe)")
    for sym, d in research.get("sector_etfs", {}).items():
        chg1d  = d.get("change_1d_pct")
        chg5d  = d.get("change_5d_pct")
        chg1mo = d.get("change_1mo_pct")
        chg3mo = d.get("change_3mo_pct")
        if chg1d is not None:
            parts.append(f"- {sym}: 1d={chg1d:+.1f}%  5d={chg5d:+.1f}%  "
                         f"1mo={chg1mo:+.1f}%  3mo={chg3mo:+.1f}%")

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

    # UCITS candidate ETF performance — grounds conviction in actual price action
    ucits_data = research.get("ucits_candidates", {})
    if ucits_data:
        parts.append("\n## UCITS Candidate ETF Performance")
        parts.append("  (5d / 1mo / 3mo returns, RSI, DMA position)")
        for sym, d in ucits_data.items():
            if d.get("error"):
                continue
            chg5d  = d.get("change_5d_pct")
            chg1mo = d.get("change_1mo_pct")
            chg3mo = d.get("change_3mo_pct")
            rsi    = d.get("rsi_14")
            above50  = "↑50d" if d.get("above_50dma") else "↓50d"
            above200 = "↑200d" if d.get("above_200dma") else "↓200d"
            c5  = f"{chg5d:+.1f}%" if chg5d is not None else "n/a"
            c1m = f"{chg1mo:+.1f}%" if chg1mo is not None else "n/a"
            c3m = f"{chg3mo:+.1f}%" if chg3mo is not None else "n/a"
            parts.append(f"  {sym}: 5d={c5}  1mo={c1m}  3mo={c3m}  RSI={rsi}  {above50} {above200}")

    # Prior weeks candidate history — for trajectory tracking (up to 4 weeks back)
    if prior_weeks:
        parts.append("\n## Prior Weeks Research — Candidate Trajectory")
        for pw in prior_weeks:
            pw_week = pw.get("week", "?")
            pw_regime = pw.get("macro_assessment", {}).get("regime", "?")
            pw_candidates = pw.get("opportunity_candidates", [])
            parts.append(f"\n  {pw_week} [{pw_regime}]:")
            for c in pw_candidates:
                parts.append(f"    - {c.get('theme')} ({c.get('ucits_instrument')}): "
                             f"conviction={c.get('conviction')}  fit={c.get('fit_for_portfolio')}")
            shifts = pw.get("structural_shifts", [])
            if shifts:
                for s in shifts:
                    parts.append(f"    ► STRUCTURAL: {s}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting market research")

    research = load_json(DATA / "context" / "market_research.json")
    agent1   = load_json(DATA / "weekly" / "agent1_analysis.json")
    agent2   = load_json(DATA / "weekly" / "agent2_risk.json")

    if not research:
        log(AGENT, "WARNING: market_research.json missing — research context will be thin")

    # Load up to 4 prior weeks of agent3 outputs for candidate trajectory tracking.
    # Prefers dated archive dirs (data/reports/YYYY-WNN/) for full history;
    # falls back to last_week_agent3.json if no archives exist yet.
    prior_weeks: list[dict] = []
    dated_dirs = sorted((DATA / "reports").glob("20??-W??"))[-4:]
    for wk_dir in dated_dirs:
        wk_json = wk_dir / "agent3_research.json"
        if wk_json.exists():
            pw = load_json(wk_json)
            if pw:
                prior_weeks.append(pw)
    if not prior_weeks:
        lw = load_json(DATA / "reports" / "last_week_agent3.json")
        if lw:
            prior_weeks = [lw]

    log(AGENT, f"Loaded {len(prior_weeks)} prior week(s) for candidate trajectory")

    scout_logs = concat_scout_logs(high_signal_scout_logs())

    # Run Haiku news filter — scores and clusters multi-day headlines cheaply
    news_digest = run_news_filter()

    context = build_context(research, agent1, agent2, scout_logs, prior_weeks, news_digest)
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
