"""
Pre-computation entrypoint.

Run this before every agent invocation — daily scouts and weekly pipeline.
Reads the IBKR CSV, fetches external data, computes derived metrics,
and writes two JSON context files:

  data/context/portfolio_snapshot.json   — consumed by Agents 1, 2, 4, scout
  data/context/market_research.json      — consumed by Agent 3 (Market Researcher)

Usage:
  python src/precompute.py

Environment:
  FRED_API_KEY   — enables macro indicators from FRED
  IBKR_CSV_PATH  — override default path to IBKR export
"""

import json
import os
import sys
import datetime
from pathlib import Path
from dotenv import load_dotenv

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent))

import parse_ibkr
import fetch_market_data as fmd
import compute
import chronicle

# ── Config ────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
CONTEXT_DIR = REPO_ROOT / "data/context"
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

PORTFOLIO_SNAPSHOT_PATH = CONTEXT_DIR / "portfolio_snapshot.json"
MARKET_RESEARCH_PATH    = CONTEXT_DIR / "market_research.json"

HISTORY_DIR = REPO_ROOT / "data/context/history"
NEWS_DIR    = REPO_ROOT / "data/news"

load_dotenv(REPO_ROOT / ".env")


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[precompute] Starting at {now}")

    # ── Step 1: Resolve IBKR CSV (fetch fresh if needed) ─────────────────────
    # IBKR_CSV_PATH env var overrides auto-resolution (useful for CI/testing).
    if os.environ.get("IBKR_CSV_PATH"):
        ibkr_csv = Path(os.environ["IBKR_CSV_PATH"])
    else:
        ibkr_csv = fmd.get_or_fetch_ibkr_csv()

    print(f"[precompute] Parsing {ibkr_csv}")
    if not ibkr_csv.exists():
        print(f"[precompute] ERROR: CSV not found at {ibkr_csv}", file=sys.stderr)
        sys.exit(1)

    sections = parse_ibkr.parse(str(ibkr_csv))

    nav           = parse_ibkr.extract_nav_history(sections.get("nav_history", []))
    holdings      = parse_ibkr.aggregate_positions(sections.get("positions", []))
    dividends     = parse_ibkr.extract_dividends_ytd(sections.get("cash_txns", []))
    deposits_ytd  = parse_ibkr.extract_deposits_ytd(sections.get("cash_txns", []))

    nav_total = nav.get("total", 0)
    symbols   = list(holdings.keys())

    print(f"[precompute] Found {len(symbols)} holdings: {symbols}")
    print(f"[precompute] NAV: ${nav_total:,.2f}")

    # ── Step 2: Fetch market data ─────────────────────────────────────────────
    print("[precompute] Fetching holdings market data...")
    holdings_mkt = fmd.fetch_holdings_data(symbols)

    print("[precompute] Fetching sector ETF data...")
    sector_data = fmd.fetch_sector_data()

    print("[precompute] Fetching macro indicators (FRED)...")
    macro = fmd.fetch_macro_fred()

    print("[precompute] Fetching market sentiment (VIX, EUR/USD)...")
    sentiment = fmd.fetch_market_sentiment()

    print("[precompute] Fetching earnings calendar...")
    earnings = fmd.fetch_earnings_calendar(symbols, days_ahead=14)

    print("[precompute] Fetching news headlines...")
    headlines = fmd.fetch_headlines(symbols)

    print("[precompute] Fetching analyst data...")
    analyst_data = fmd.fetch_analyst_data(symbols)

    # ── Step 3: Compute derived metrics ──────────────────────────────────────
    allocation   = compute.compute_allocation(holdings, nav_total)
    performance  = compute.compute_performance(nav, deposits_ytd)
    risk_proxies = compute.compute_risk_proxies(holdings, holdings_mkt, nav_total)
    stress       = compute.compute_stress_scenarios(holdings, nav_total)
    flags        = compute.compute_holding_flags(holdings, holdings_mkt)

    # Enrich holdings with live market data
    holdings_enriched = {}
    for symbol, h in holdings.items():
        mkt = holdings_mkt.get(symbol, {})
        holdings_enriched[symbol] = {**h, **{
            "current_price":      mkt.get("current_price"),
            "change_1d_pct":      mkt.get("change_1d_pct"),
            "change_5d_pct":      mkt.get("change_5d_pct"),
            "change_1mo_pct":     mkt.get("change_1mo_pct"),
            "52w_high":           mkt.get("52w_high"),
            "52w_low":            mkt.get("52w_low"),
            "pct_from_52w_high":  mkt.get("pct_from_52w_high"),
            "pe_ratio":           mkt.get("pe_ratio"),
            "dividend_yield":     mkt.get("dividend_yield"),
            "sector":             mkt.get("sector"),
        }}

    # ── Step 4: Write portfolio_snapshot.json ────────────────────────────────
    chronicle_summary = chronicle.summarise_for_context(weeks=12)

    snapshot = {
        "generated_at": now,
        "nav": nav,
        "holdings": holdings_enriched,
        "allocation": allocation,
        "performance": performance,
        "risk": {**risk_proxies, "stress_scenarios": stress},
        "holding_flags": flags,
        "dividends": dividends,
        "deposits_ytd": deposits_ytd,
        "macro": macro,
        "sentiment": sentiment,
        "market_chronicle": chronicle_summary,
    }

    PORTFOLIO_SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"[precompute] Wrote {PORTFOLIO_SNAPSHOT_PATH}")

    # ── Step 5: Write market_research.json ───────────────────────────────────
    research = {
        "generated_at": now,
        "sector_etfs": sector_data,
        "macro": macro,
        "sentiment": sentiment,
        "events_calendar": earnings,
        "headlines": headlines,
        "analyst_data": analyst_data,
        "current_holdings": symbols,
        "market_chronicle": chronicle_summary,
    }

    MARKET_RESEARCH_PATH.write_text(json.dumps(research, indent=2, default=str))
    print(f"[precompute] Wrote {MARKET_RESEARCH_PATH}")

    # ── Step 6: Archive dated copies for historical access ────────────────────
    today_str = datetime.date.today().isoformat()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    NEWS_DIR.mkdir(parents=True, exist_ok=True)

    dated_snapshot = HISTORY_DIR / f"portfolio_snapshot_{today_str}.json"
    dated_research = HISTORY_DIR / f"market_research_{today_str}.json"
    dated_snapshot.write_text(json.dumps(snapshot, indent=2, default=str))
    dated_research.write_text(json.dumps(research, indent=2, default=str))
    print(f"[precompute] Archived dated snapshot → {dated_snapshot.name}")

    # Save headlines separately for multi-day news access by agents
    news_path = NEWS_DIR / f"{today_str}.json"
    news_path.write_text(json.dumps(headlines, indent=2, default=str))
    print(f"[precompute] Archived news → {news_path.name}")

    print("[precompute] Done.")
    _print_summary(snapshot)


def _print_summary(snapshot: dict) -> None:
    nav = snapshot["nav"]
    print(f"\n{'─'*50}")
    print(f"  NAV:     ${nav.get('total', 0):>10,.2f}")
    print(f"  Cash:    ${nav.get('cash', 0):>10,.2f}")
    print(f"  Invested:${nav.get('stock', 0):>10,.2f}")
    print(f"\n  Holdings:")
    for h in snapshot["holdings"].values():
        pnl = h.get("unrealized_pnl", 0)
        pct = h.get("unrealized_pnl_pct", 0)
        chg = h.get("change_1d_pct")
        chg_str = f"{chg:+.1f}% today" if chg is not None else "no live price"
        print(f"    {h['symbol']:<6}  ${h['position_value']:>8,.2f}  PnL: {pnl:+.2f} ({pct:+.1f}%)  {chg_str}")

    flags = snapshot.get("holding_flags", [])
    if flags:
        print(f"\n  Flags:")
        for f in flags:
            print(f"    [{f['flag']}] {f['symbol']}: {f['reason']}")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    run()
