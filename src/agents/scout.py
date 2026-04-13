import os
"""
Daily Scout Agent

Runs every morning (Mon–Sat) before markets open.
Reads pre-computed portfolio snapshot and market research,
cross-references the last 3 days of logs, and writes a structured
daily briefing note consumed by the weekly agents.

Output: data/scout_logs/YYYY-WNN-<day>.md
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_investor_profile, load_json, load_text, run_claude, write_text,
    scout_logs_this_week, current_week, log, load_prompt,
)

AGENT = "scout"
MAX_TURNS = 15

_SYSTEM_PROMPT_TEMPLATE = load_prompt("scout")


def build_context(snapshot: dict, research: dict, prior_logs: list[Path]) -> str:
    parts = []

    # Today's date
    today = datetime.date.today().isoformat()
    parts.append(f"# Daily Scout Context — {today}\n")

    investor_profile = load_investor_profile()
    parts.append(f"# Investor profile: \n{investor_profile}")

    # Portfolio snapshot (key fields only — keep tokens lean)
    nav = snapshot.get("nav", {})
    parts.append(f"## Portfolio NAV\nTotal: ${nav.get('total', 0):,.2f}  "
                 f"Cash: ${nav.get('cash', 0):,.2f}  "
                 f"Invested: ${nav.get('stock', 0):,.2f}\n")

    parts.append("## Holdings")
    for sym, h in snapshot.get("holdings", {}).items():
        chg = h.get("change_1d_pct")
        chg_str = f"{chg:+.1f}% today" if chg is not None else "no live price"
        pnl = h.get("unrealized_pnl_pct", 0)
        parts.append(f"- {sym}: ${h.get('position_value', 0):,.2f}  "
                     f"PnL: {pnl:+.1f}%  {chg_str}")

    parts.append("\n## Holding Flags")
    flags = snapshot.get("holding_flags", [])
    if flags:
        for f in flags:
            parts.append(f"- [{f['flag']}] {f['symbol']}: {f['reason']}")
    else:
        parts.append("- None")

    # Sentiment
    s = snapshot.get("sentiment", {})
    parts.append(f"\n## Market Sentiment")
    parts.append(f"- VIX: {s.get('vix')} ({s.get('vix_regime')})  "
                 f"5d change: {s.get('vix_change_5d')}")
    parts.append(f"- EUR/USD: {s.get('eurusd')}  {s.get('eurusd_note', '')}")
    parts.append(f"- S&P 500 P/E: {s.get('sp500_trailing_pe')}")

    # Macro
    m = snapshot.get("macro", {})
    parts.append(f"\n## Macro Indicators")
    parts.append(f"- 10y yield: {m.get('t10y')}%  |  2y yield: {m.get('t2y')}%  "
                 f"|  Spread: {m.get('yield_curve_spread')}%")
    parts.append(f"- Fed Funds: {m.get('fed_funds')}%  |  CPI YoY: {m.get('cpi_yoy')}%  "
                 f"|  Core CPI YoY: {m.get('core_cpi')}%  |  PCE YoY: {m.get('pce_yoy')}%")
    parts.append(f"- Unemployment: {m.get('unemployment')}%  "
                 f"|  Retail Sales MoM: {m.get('retail_sales_mom')}%  "
                 f"|  Industrial Prod: {m.get('industrial_prod')}")
    cli = m.get("leading_indicator")
    cli_regime = m.get("leading_indicator_regime", "")
    parts.append(f"- OECD Leading Indicator: {cli} ({cli_regime})  "
                 f"|  Consumer Sent: {m.get('consumer_sent')}")
    parts.append(f"- HY Credit Spread: {m.get('hy_spread')}%")

    # Fear & Greed
    fg = snapshot.get("fear_greed", {})
    if fg and not fg.get("error"):
        score = fg.get("score")
        label = fg.get("label", "")
        s1w   = fg.get("score_1w_ago")
        l1w   = fg.get("label_1w_ago", "")
        score_str = f"{score:.0f}" if score is not None else "n/a"
        s1w_str   = f"{s1w:.0f} ({l1w})" if s1w is not None else "n/a"
        parts.append(f"- Fear & Greed: {score_str} — {label}  (1w ago: {s1w_str})")

    # Sector performance
    parts.append("\n## Sector ETF Performance (1d)")
    for sym, d in research.get("sector_etfs", {}).items():
        chg = d.get("change_1d_pct")
        if chg is not None:
            parts.append(f"- {sym} ({d.get('symbol', sym)}): {chg:+.1f}%")

    # Events calendar
    events = research.get("events_calendar", [])
    parts.append("\n## Upcoming Events (next 14 days)")
    if events:
        for e in events:
            parts.append(f"- {e['date']} ({e['days_until']}d): {e['type']} — {e.get('symbol', 'macro')}")
    else:
        parts.append("- No earnings or scheduled events found")

    # Headlines
    headlines = research.get("headlines", [])
    parts.append("\n## News Headlines")
    if headlines:
        for h in headlines[:15]:
            parts.append(f"- [{h['source']}] {h['title']}")
    else:
        parts.append("- No headlines fetched")

    # Prior scout logs
    if prior_logs:
        parts.append("\n## Prior Scout Logs (last 3 days — for pattern detection)")
        for p in prior_logs[-3:]:
            parts.append(f"\n### {p.stem}\n{p.read_text()}")

    # Market chronicle
    chronicle = snapshot.get("market_chronicle", "")
    if chronicle and "No historical" not in chronicle:
        parts.append(f"\n{chronicle}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting daily scout")


    snapshot_path = DATA / "context" / "portfolio_snapshot.json"
    research_path = DATA / "context" / "market_research.json"

    snapshot = load_json(snapshot_path)
    research = load_json(research_path)

    # If snapshot is missing, run precompute.py and retry
    if not snapshot:
        log(AGENT, "portfolio_snapshot.json not found. Running precompute.py...")
        import subprocess
        result = subprocess.run([sys.executable, str(Path(__file__).parent.parent / "precompute.py")], capture_output=True, text=True)
        if result.returncode != 0:
            log(AGENT, f"precompute.py failed: {result.stderr.strip()}")
            sys.exit(1)
        log(AGENT, "precompute.py completed. Retrying snapshot load...")
        snapshot = load_json(snapshot_path)
        if not snapshot:
            log(AGENT, "ERROR: portfolio_snapshot.json still not found after running precompute.py.")
            sys.exit(1)

    # Inject current holdings into system prompt dynamically
    holdings_list = ", ".join(snapshot.get("holdings", {}).keys()) or "no holdings found"
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace("{holdings_list}", holdings_list)

    prior_logs = scout_logs_this_week()
    context = build_context(snapshot, research, prior_logs)

    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get('AGENTFOLIO_MAX_TURNS', MAX_TURNS))})...")
    output = run_claude(system_prompt, context, MAX_TURNS)

    # Determine output path
    today = datetime.date.today()
    week = current_week()
    day_abbr = today.strftime("%a").lower()  # mon, tue, etc.
    out_path = DATA / "scout_logs" / f"{week}-{day_abbr}.md"

    write_text(out_path, output)
    log(AGENT, f"Wrote {out_path}")
    

    # Print first line as quick status
    first_line = output.split("\n")[0]
    log(AGENT, f"Report: {first_line}")


if __name__ == "__main__":
    run()
