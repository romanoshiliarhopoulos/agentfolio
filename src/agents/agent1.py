import os
"""
Agent 1 — Portfolio Analyzer

Deep multi-pass analysis of the current portfolio state.
Runs Sunday Week A at 02:00 AM.

Reads:
  data/context/portfolio_snapshot.json
  data/scout_logs/YYYY-WNN-weekly.md  (week digest)
  data/reports/last_week_agent1.json  (prior week trend baseline)

Writes:
  data/weekly/agent1_analysis.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base import (
    DATA, load_json, load_text, load_investor_profile,
    run_claude, extract_json, write_json,
    scout_logs_this_week, concat_scout_logs, current_week, log, load_prompt,
)

AGENT = "agent1"
MAX_TURNS = 40

SYSTEM_PROMPT = load_prompt("agent1")


def build_context(snapshot: dict, weekly_digest: str, last_week: dict) -> str:
    parts = []
    week = current_week()
    parts.append(f"# Portfolio Analysis Context — {week}\n")

    # Full portfolio snapshot
    parts.append("## Portfolio Snapshot")
    nav = snapshot.get("nav", {})
    parts.append(f"NAV: ${nav.get('total', 0):,.2f}  |  "
                 f"Cash: ${nav.get('cash', 0):,.2f} ({nav.get('cash', 0) / max(nav.get('total', 1), 1) * 100:.1f}%)  |  "
                 f"Invested: ${nav.get('stock', 0):,.2f}")
    parts.append(f"NAV 7d ago: ${nav.get('total_7d_ago') or 0:,.2f}  |  "
                 f"NAV 30d ago: ${nav.get('total_30d_ago') or 0:,.2f}")

    parts.append("\n## Holdings Detail")
    for sym, h in snapshot.get("holdings", {}).items():
        parts.append(
            f"\n### {sym} — {h.get('description', '')}\n"
            f"  Quantity: {h.get('quantity', 0):.4f} shares  |  "
            f"Mark price: ${h.get('mark_price', 0):,.2f}  |  "
            f"Position value: ${h.get('position_value', 0):,.2f}\n"
            f"  Cost basis (avg): ${h.get('weighted_avg_cost', 0):,.2f}  |  "
            f"Total cost: ${h.get('total_cost_basis', 0):,.2f}\n"
            f"  Unrealised PnL: ${h.get('unrealized_pnl', 0):+,.2f} "
            f"({h.get('unrealized_pnl_pct', 0):+.1f}%)\n"
            f"  First opened: {h.get('first_opened')}  |  "
            f"Days held: {h.get('days_held')}  |  Lots: {h.get('lot_count')}\n"
            f"  1d: {h.get('change_1d_pct', 'n/a')}%  |  "
            f"5d: {h.get('change_5d_pct', 'n/a')}%  |  "
            f"1mo: {h.get('change_1mo_pct', 'n/a')}%\n"
            f"  52w high: ${h.get('52w_high') or 'n/a'}  |  "
            f"52w low: ${h.get('52w_low') or 'n/a'}  |  "
            f"From 52w high: {h.get('pct_from_52w_high', 'n/a')}%\n"
            f"  P/E: {h.get('pe_ratio', 'n/a')}  |  "
            f"Div yield: {h.get('dividend_yield', 'n/a')}  |  "
            f"Sector: {h.get('sector', 'n/a')}"
        )

    parts.append("\n## Allocation (pre-computed)")
    alloc = snapshot.get("allocation", {})
    for a in alloc.get("by_symbol", []):
        parts.append(f"  {a['symbol']:6}  {a['allocation_pct']:5.1f}%  "
                     f"(target: {a['equal_weight_target_pct']:.1f}%  "
                     f"drift: {a['drift_from_equal_weight_pct']:+.1f}%)")

    parts.append("\n## Pre-computed Risk Proxies")
    risk = snapshot.get("risk", {})
    parts.append(f"  Beta (approx): {risk.get('portfolio_beta_approx', 'n/a')}  "
                 f"(coverage: {risk.get('beta_coverage_pct', 'n/a')}%)")
    parts.append(f"  Largest position: {risk.get('concentration_top1_pct', 'n/a'):.1f}%")
    parts.append("\n  Stress scenarios:")
    for s in risk.get("stress_scenarios", []):
        parts.append(f"    {s['scenario']}: {s['estimated_impact_usd']:+,.0f} "
                     f"({s['impact_pct_of_nav']:+.1f}% of NAV)")

    parts.append("\n## Holding Flags (pre-computed)")
    for f in snapshot.get("holding_flags", []):
        parts.append(f"  [{f['flag']}] {f['symbol']}: {f['reason']}")

    parts.append("\n## Dividends YTD")
    div = snapshot.get("dividends", {})
    parts.append(f"  Gross: ${div.get('dividends_ytd', 0):.2f}  |  "
                 f"Withholding: ${div.get('withholding_ytd', 0):.2f}  |  "
                 f"Net: ${div.get('net_dividends_ytd', 0):.2f}")

    parts.append("\n## Market Sentiment")
    s = snapshot.get("sentiment", {})
    parts.append(f"  VIX: {s.get('vix')} ({s.get('vix_regime')})  "
                 f"| EUR/USD: {s.get('eurusd')} ({s.get('eurusd_change_1mo_pct'):+.1f}% 1mo)  "
                 f"| S&P P/E: {s.get('sp500_trailing_pe')}")
    m = snapshot.get("macro", {})
    parts.append(f"  HY spread: {m.get('hy_spread')}%  "
                 f"| 10y-2y spread: {m.get('yield_curve_spread')}%  "
                 f"| CPI: {m.get('cpi_yoy')}%")

    parts.append("\n## Weekly Scout Digest")
    parts.append(weekly_digest if weekly_digest else "No scout logs available for this week.")

    if last_week:
        parts.append("\n## Last Week's Portfolio Analysis (for comparison)")
        parts.append(json.dumps(last_week, indent=2))

    chronicle = snapshot.get("market_chronicle", "")
    if chronicle and "No historical" not in chronicle:
        parts.append(f"\n{chronicle}")

    return "\n".join(parts)


def run() -> None:
    log(AGENT, "Starting portfolio analysis")

    snapshot = load_json(DATA / "context" / "portfolio_snapshot.json")
    if not snapshot:
        log(AGENT, "ERROR: portfolio_snapshot.json not found. Run precompute.py first.")
        sys.exit(1)

    # Build weekly digest from all scout logs this week
    all_logs = scout_logs_this_week()
    weekly_digest = concat_scout_logs(all_logs)
    log(AGENT, f"Loaded {len(all_logs)} scout logs for digest")

    last_week = load_json(DATA / "reports" / "last_week_agent1.json")

    context = build_context(snapshot, weekly_digest, last_week)
    log(AGENT, f"Built context ({len(context)} chars). Calling claude (max_turns={int(os.environ.get("AGENTFOLIO_MAX_TURNS", MAX_TURNS))})...")

    raw = run_claude(SYSTEM_PROMPT, context, MAX_TURNS)
    result = extract_json(raw)
    result["_generated_at"] = __import__("datetime").datetime.now().isoformat()

    out_path = DATA / "weekly" / "agent1_analysis.json"
    write_json(out_path, result)
    log(AGENT, f"Wrote {out_path}")


if __name__ == "__main__":
    run()
