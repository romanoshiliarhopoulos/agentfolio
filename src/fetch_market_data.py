"""
Market data fetcher.

Pulls price data, macro indicators, sector performance, earnings calendar,
and news headlines from free sources. No paid API keys required except
FRED (free registration at fred.stlouisfed.org).

Set FRED_API_KEY in env
"""

import os
import datetime
import feedparser
import yfinance as yf
from typing import Any

# ── IBKR Flex Web Service integration ───────────────────────────────────────
import time
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

IBKR_FLEX_SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
IBKR_DATA_DIR = Path(__file__).parent.parent / "data" / "ibkr"


def fetch_ibkr_flex_report(
    token: str,
    query_id: str,
    max_retries: int = 10,
    retry_delay: float = 5.0,
) -> str:
    """
    Fetches the IBKR Flex statement via the mandatory two-step process.

    Step 1 — SendRequest: queues the report generation, returns a ReferenceCode
             and a polling URL.
    Step 2 — GetStatement: polls that URL until the report is ready, returns
             the full statement XML.

    Args:
        token:       IBKR Flex token (IBKR_ACTIVATION_TOKEN in .env)
        query_id:    Flex Query ID configured in IBKR account management
        max_retries: Poll attempts before raising TimeoutError (default 10)
        retry_delay: Seconds between poll attempts (default 5s → 50s max wait)
    Returns:
        Full Flex statement XML as a string.
    Raises:
        RuntimeError if IBKR returns an error status.
        TimeoutError if the report is not ready within max_retries attempts.
    """
    # Step 1: SendRequest — queue the report
    resp = requests.get(
        IBKR_FLEX_SEND_URL,
        params={"t": token, "q": query_id, "v": "3"},
        timeout=30,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    status = root.findtext("Status")
    if status != "Success":
        error_msg = root.findtext("ErrorMessage") or f"status={status}"
        raise RuntimeError(f"IBKR Flex SendRequest failed: {error_msg}")

    reference_code = root.findtext("ReferenceCode")
    poll_url = root.findtext("Url")
    if not reference_code or not poll_url:
        raise RuntimeError("IBKR response missing ReferenceCode or Url")

    print(f"[IBKR] Report queued (ref={reference_code}), polling for result...")

    # Step 2: GetStatement — poll until the report is generated
    for attempt in range(1, max_retries + 1):
        time.sleep(retry_delay)
        poll_resp = requests.get(
            poll_url,
            params={"q": reference_code, "t": token, "v": "3"},
            timeout=30,
        )
        poll_resp.raise_for_status()
        text = poll_resp.text.strip()

        # IBKR signals "still generating" via a FlexStatementResponse with Status=Processing
        if "<Status>Processing</Status>" in text or "Statement generation in progress" in text:
            print(f"[IBKR] Not ready yet (attempt {attempt}/{max_retries})...")
            continue

        print(f"[IBKR] Statement received on attempt {attempt}.")
        return text

    raise TimeoutError(
        f"IBKR Flex report not ready after {max_retries} retries "
        f"({max_retries * retry_delay:.0f}s total)"
    )


def fetch_and_update_ibkr_portfolio() -> "Path | None":
    """
    Fetches the IBKR Flex report and saves it to data/ibkr/ with a date stamp.

    Saved as: data/ibkr/flex_YYYY-MM-DD.xml
    Returns the path to the saved file, or None if env vars are missing.
    """
    token = os.environ.get("IBKR_ACTIVATION_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    if not token or not query_id:
        print("[IBKR] IBKR_ACTIVATION_TOKEN or IBKR_FLEX_QUERY_ID not set. Skipping.")
        return None

    xml = fetch_ibkr_flex_report(token, query_id)

    today = datetime.date.today().isoformat()
    IBKR_DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = IBKR_DATA_DIR / f"flex_{today}.csv"
    output_path.write_text(xml, encoding="utf-8")
    print(f"[IBKR] Saved Flex report → {output_path}")
    return output_path


def get_or_fetch_ibkr_csv() -> Path:
    """
    Returns the path to the best available IBKR Flex CSV:

    1. Today's file (data/ibkr/flex_YYYY-MM-DD.csv) — skips a network call if
       it was already fetched earlier today.
    2. Fetch fresh — if IBKR_ACTIVATION_TOKEN + IBKR_FLEX_QUERY_ID are set and
       today's file doesn't exist yet.
    3. Most recent existing flex_*.csv — if credentials are missing or the fetch
       fails.
    4. data/ibkr/Portfolio_status.csv — final fallback (manually downloaded).
    """
    today = datetime.date.today().isoformat()
    today_path = IBKR_DATA_DIR / f"flex_{today}.csv"

    if today_path.exists():
        print(f"[IBKR] Using today's cached report: {today_path}")
        return today_path

    # Try to fetch a fresh report
    token = os.environ.get("IBKR_ACTIVATION_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    if token and query_id:
        try:
            path = fetch_and_update_ibkr_portfolio()
            if path and path.exists():
                return path
        except Exception as e:
            print(f"[IBKR] Fetch failed ({e}), falling back to most recent file.")

    # Fall back to most recent saved flex file
    flex_files = sorted(IBKR_DATA_DIR.glob("flex_*.csv"))
    if flex_files:
        latest = flex_files[-1]
        print(f"[IBKR] Using most recent flex file: {latest}")
        return latest

    # Final fallback: manually downloaded CSV
    fallback = IBKR_DATA_DIR / "Portfolio_status.csv"
    print(f"[IBKR] Falling back to {fallback}")
    return fallback


def test_ibkr_flex_statement():
    """Run the full two-step fetch and print the first 500 chars of the saved statement."""
    try:
        path = fetch_and_update_ibkr_portfolio()
        if path:
            print(f"--- IBKR Flex Statement saved to {path} ---")
            print(path.read_text(encoding="utf-8")[:500])
    except Exception as e:
        print(f"[ERROR] Failed to fetch IBKR Flex statement: {e}")


if __name__ == "__main__":
    test_ibkr_flex_statement()

# ── Sector ETFs used as broad market pulse ──────────────────────────────────

# IBKR symbol → Yahoo Finance ticker.
# Required for all non-US-listed instruments — Yahoo Finance needs the exchange suffix.
# Exchange suffixes: .L = LSE (USD or GBP*), .DE = Xetra (EUR), .AS = Euronext Amsterdam (EUR)
# * GBP-priced LSE ETFs quote in pence on Yahoo Finance (divide by 100 for £). USD share classes preferred.
# Add new holdings here whenever you buy a European-listed instrument.
YAHOO_TICKER_MAP: dict[str, str] = {
    # ── S&P 500 ───────────────────────────────────────────────────────────────
    "VUAA":  "VUAA.L",    # Vanguard S&P 500 UCITS ETF — USD, LSE, Acc (held)
    "CSPX":  "CSPX.L",   # iShares Core S&P 500 UCITS ETF — USD, LSE, Acc
    "SXR8":  "SXR8.DE",  # iShares Core S&P 500 UCITS ETF — EUR, Xetra, Acc
    "IUSA":  "IUSA.L",   # iShares Core S&P 500 UCITS ETF — GBP*, LSE, Dist
    "VUSD":  "VUSD.L",   # Vanguard S&`P 500 UCITS ETF — USD, LSE, Dist

    # ── Nasdaq-100 ────────────────────────────────────────────────────────────
    "EQQQ":  "EQQQ.L",   # Invesco Nasdaq-100 UCITS ETF — USD, LSE, Dist (held)
    "CNDX":  "CNDX.L",   # iShares Nasdaq 100 UCITS ETF — USD, LSE, Acc
    "SXRV":  "SXRV.DE",  # iShares Nasdaq 100 UCITS ETF — EUR, Xetra, Acc

    # ── MSCI World ────────────────────────────────────────────────────────────
    "SWDA":  "SWDA.L",   # iShares Core MSCI World UCITS ETF — USD, LSE, Acc
    "IWDA":  "IWDA.AS",  # iShares Core MSCI World UCITS ETF — EUR, Euronext Amsterdam, Acc
    "EUNL":  "EUNL.DE",  # iShares Core MSCI World UCITS ETF — EUR, Xetra, Acc

    # ── FTSE All-World (global incl. EM) ─────────────────────────────────────
    "VWRL":  "VWRL.L",   # Vanguard FTSE All-World UCITS ETF — USD, LSE, Dist
    "VWCE":  "VWCE.DE",  # Vanguard FTSE All-World UCITS ETF — EUR, Xetra, Acc

    # ── Emerging Markets ─────────────────────────────────────────────────────
    "VFEM":  "VFEM.L",   # Vanguard FTSE Emerging Markets UCITS ETF — USD, LSE, Dist
    "IEEM":  "IEEM.L",   # iShares Core MSCI EM IMI UCITS ETF — USD, LSE, Acc
    "IS3N":  "IS3N.DE",  # iShares Core MSCI EM IMI UCITS ETF — EUR, Xetra, Acc

    # ── Europe ────────────────────────────────────────────────────────────────
    "VEUR":  "VEUR.L",   # Vanguard FTSE Developed Europe UCITS ETF — USD, LSE, Dist
    "IMEU":  "IMEU.L",   # iShares Core MSCI Europe UCITS ETF — USD, LSE, Acc
    "MEUD":  "MEUD.PA",  # Amundi MSCI Europe UCITS ETF — EUR, Euronext Paris, Dist

    # ── US Treasuries ─────────────────────────────────────────────────────────
    "IDTL":  "IDTL.L",   # iShares $ Treasury Bond 20+yr UCITS ETF — USD, LSE
    "DTLA":  "DTLA.DE",  # iShares $ Treasury Bond 20+yr UCITS ETF — EUR, Xetra
    "IBTS":  "IBTS.L",   # iShares $ Treasury Bond 1-3yr UCITS ETF — USD, LSE
    "IBTM":  "IBTM.L",   # iShares $ Treasury Bond 7-10yr UCITS ETF — USD, LSE

    # ── Global Aggregate Bonds ────────────────────────────────────────────────
    "VAGP":  "VAGP.L",   # Vanguard Global Aggregate Bond UCITS ETF — USD, LSE, Acc
    "AGGG":  "AGGG.L",   # iShares Core Global Aggregate Bond UCITS ETF — USD, LSE, Acc

    # ── Corporate Bonds ───────────────────────────────────────────────────────
    "IHYG":  "IHYG.L",   # iShares $ High Yield Corporate Bond UCITS ETF — USD, LSE
    "SLQD":  "SLQD.L",   # iShares $ Corp Bond 0-3yr UCITS ETF — USD, LSE
    "LQDE":  "LQDE.L",   # iShares $ Corporate Bond UCITS ETF — USD, LSE

    # ── Sector ETFs (iShares on Xetra) ───────────────────────────────────────
    "QDVE":  "QDVE.DE",  # iShares S&P 500 IT Sector UCITS ETF — EUR, Xetra
    "QDVH":  "QDVH.DE",  # iShares S&P 500 Healthcare Sector UCITS ETF — EUR, Xetra
    "QDVF":  "QDVF.DE",  # iShares S&P 500 Financials Sector UCITS ETF — EUR, Xetra
    "QDVD":  "QDVD.DE",  # iShares S&P 500 Energy Sector UCITS ETF — EUR, Xetra
    "QDVC":  "QDVC.DE",  # iShares S&P 500 Consumer Disc. Sector UCITS ETF — EUR, Xetra

    # ── Gold & Commodities ────────────────────────────────────────────────────
    "IGLN":  "IGLN.L",   # iShares Physical Gold ETC — USD, LSE
    "SGLD":  "SGLD.L",   # Invesco Physical Gold ETC — USD, LSE
    "PHAU":  "PHAU.L",   # WisdomTree Physical Gold ETC — USD, LSE
    "BCOG":  "BCOG.L",   # iShares Diversified Commodity Swap UCITS ETF — USD, LSE
}


def yahoo_ticker(ibkr_symbol: str) -> str:
    """Return the Yahoo Finance ticker for a given IBKR symbol."""
    return YAHOO_TICKER_MAP.get(ibkr_symbol, ibkr_symbol)


SECTOR_ETFS = {
    "SPY":  "S&P 500",
    "QQQ":  "Nasdaq-100",
    "IWM":  "Russell 2000 (Small Cap)",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLK":  "Technology",
    "XLV":  "Healthcare",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "VNQ":  "Real Estate",
    "GLD":  "Gold",
    "TLT":  "Long-Term Treasuries",
}

# ── News RSS feeds ───────────────────────────────────────────────────────────

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else round(f, 4)  # NaN check
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / prior * 100, 2)


# ── Stock / ETF data ─────────────────────────────────────────────────────────

def fetch_symbol_data(symbol: str) -> dict[str, Any]:
    """
    Fetch price, performance, and fundamental data for a single symbol via yfinance.
    Uses a 35-day history window to compute 1d, 5d, and 1mo changes.
    Translates IBKR symbols to Yahoo Finance tickers where needed (e.g. LSE listings).
    """
    try:
        yf_symbol = yahoo_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="35d")
        info = ticker.info

        if hist.empty:
            return {"symbol": symbol, "yahoo_ticker": yf_symbol, "error": "no_data"}

        prices = hist["Close"].dropna()
        current_price = _safe_float(prices.iloc[-1])
        price_1d_ago  = _safe_float(prices.iloc[-2]) if len(prices) >= 2 else None
        price_5d_ago  = _safe_float(prices.iloc[-6]) if len(prices) >= 6 else None
        price_1mo_ago = _safe_float(prices.iloc[0])   if len(prices) >= 20 else None

        fifty_two_week_high = _safe_float(info.get("fiftyTwoWeekHigh"))
        fifty_two_week_low  = _safe_float(info.get("fiftyTwoWeekLow"))

        pct_from_high = None
        if current_price and fifty_two_week_high:
            pct_from_high = round((current_price - fifty_two_week_high) / fifty_two_week_high * 100, 2)

        return {
            "symbol": symbol,
            "yahoo_ticker": yf_symbol,
            "current_price": current_price,
            "change_1d_pct":  _pct_change(current_price, price_1d_ago),
            "change_5d_pct":  _pct_change(current_price, price_5d_ago),
            "change_1mo_pct": _pct_change(current_price, price_1mo_ago),
            "52w_high": fifty_two_week_high,
            "52w_low":  fifty_two_week_low,
            "pct_from_52w_high": pct_from_high,
            "pe_ratio":       _safe_float(info.get("trailingPE")),
            "forward_pe":     _safe_float(info.get("forwardPE")),
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "avg_volume":     info.get("averageVolume"),
            "market_cap":     info.get("marketCap"),
            "sector":         info.get("sector"),
        }
    except Exception as e:
        return {"symbol": symbol, "yahoo_ticker": yahoo_ticker(symbol), "error": str(e)}


def fetch_holdings_data(symbols: list[str]) -> dict[str, dict]:
    return {sym: fetch_symbol_data(sym) for sym in symbols}


def fetch_sector_data() -> dict[str, dict]:
    return {sym: fetch_symbol_data(sym) for sym in SECTOR_ETFS}


def fetch_market_sentiment() -> dict[str, Any]:
    """
    Fetch VIX and EUR/USD — two fast signals with no API key required.

    VIX: fear/complacency gauge. Contextualises whether a week's moves are
    happening in a panicked or complacent market. Historical anchors:
      <15 = complacent, 15–25 = normal, 25–35 = elevated fear, >35 = crisis.

    EUR/USD: currency context for a European account holding USD-denominated
    assets. A strengthening EUR silently erodes returns even when USD prices
    rise. Agents should factor this in when assessing portfolio performance
    and when comparing European-listed vs US-listed alternatives.
    """
    result: dict[str, Any] = {}

    # VIX
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="35d")
        if not hist.empty:
            prices = hist["Close"].dropna()
            current = _safe_float(prices.iloc[-1])
            prior_5d = _safe_float(prices.iloc[-6]) if len(prices) >= 6 else None
            prior_1mo = _safe_float(prices.iloc[0]) if len(prices) >= 20 else None
            result["vix"] = current
            result["vix_5d_ago"] = prior_5d
            result["vix_1mo_ago"] = prior_1mo
            result["vix_change_5d"] = round(current - prior_5d, 2) if current and prior_5d else None
            result["vix_regime"] = (
                "COMPLACENT" if current and current < 15 else
                "NORMAL"     if current and current < 25 else
                "ELEVATED"   if current and current < 35 else
                "CRISIS"     if current else None
            )
    except Exception as e:
        result["vix"] = None
        result["vix_error"] = str(e)

    # EUR/USD
    try:
        eurusd = yf.Ticker("EURUSD=X")
        hist = eurusd.history(period="35d")
        if not hist.empty:
            prices = hist["Close"].dropna()
            current = _safe_float(prices.iloc[-1])
            prior_5d = _safe_float(prices.iloc[-6]) if len(prices) >= 6 else None
            prior_1mo = _safe_float(prices.iloc[0]) if len(prices) >= 20 else None
            result["eurusd"] = current
            result["eurusd_change_5d_pct"] = _pct_change(current, prior_5d)
            result["eurusd_change_1mo_pct"] = _pct_change(current, prior_1mo)
            # Direction note for agents: stronger EUR = lower USD returns in EUR terms
            if result.get("eurusd_change_5d_pct") is not None:
                chg = result["eurusd_change_5d_pct"]
                result["eurusd_note"] = (
                    f"EUR strengthened {chg:+.2f}% vs USD this week — USD-denominated holdings worth less in EUR terms."
                    if chg > 0.5 else
                    f"EUR weakened {chg:+.2f}% vs USD this week — USD-denominated holdings worth more in EUR terms."
                    if chg < -0.5 else
                    "EUR/USD roughly flat this week."
                )
    except Exception as e:
        result["eurusd"] = None
        result["eurusd_error"] = str(e)

    # S&P 500 trailing P/E — valuation context for long-term positioning.
    # Not the Shiller CAPE (10-year smoothed) but the closest freely available proxy.
    # Historical anchors: ~15 = historical avg, >25 = expensive, >30 = very expensive.
    try:
        sp500 = yf.Ticker("SPY")
        pe = _safe_float(sp500.info.get("trailingPE"))
        result["sp500_trailing_pe"] = pe
        result["sp500_trailing_pe_note"] = (
            "Below historical avg (~15) — market relatively cheap on trailing earnings." if pe and pe < 15 else
            "Near historical avg (~15–20) — market fairly valued on trailing earnings."  if pe and pe < 20 else
            "Above historical avg — market expensive on trailing earnings."              if pe and pe < 30 else
            "Well above historical avg (>30) — market very expensive on trailing earnings." if pe else None
        )
    except Exception as e:
        result["sp500_trailing_pe"] = None
        result["sp500_trailing_pe_error"] = str(e)

    return result


# ── Macro indicators (FRED) ───────────────────────────────────────────────────

FRED_SERIES = {
    "t10y":           "DGS10",          # 10-year Treasury yield
    "t2y":            "DGS2",           # 2-year Treasury yield
    "fed_funds":      "FEDFUNDS",       # Effective Fed Funds Rate
    "cpi_yoy":        "CPIAUCSL",       # CPI (we compute YoY manually)
    "unemployment":   "UNRATE",         # Unemployment rate
    "hy_spread":      "BAMLH0A0HYM2",   # High-yield credit spread (OAS) — recession leading indicator
}


def fetch_macro_fred() -> dict[str, Any]:
    """
    Fetch key macro series from FRED. Requires FRED_API_KEY env var.
    Falls back to None values gracefully if key is missing or API is unreachable.
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return {k: None for k in FRED_SERIES} | {"error": "FRED_API_KEY not set"}

    import requests

    results: dict[str, Any] = {}
    base = "https://api.stlouisfed.org/fred/series/observations"

    for label, series_id in FRED_SERIES.items():
        try:
            # Fetch 15 months for CPI (need 13 valid after filtering "."), 3 for others
            limit = 15 if label == "cpi_yoy" else 3
            resp = requests.get(base, params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            }, timeout=10)
            resp.raise_for_status()
            obs = [o for o in resp.json()["observations"] if o["value"] != "."]
            if not obs:
                results[label] = None
                continue

            latest_val = _safe_float(obs[0]["value"])
            results[f"{label}_date"] = obs[0]["date"]

            if label == "cpi_yoy":
                # Find observation closest to 12 months prior by date
                latest_date = datetime.date.fromisoformat(obs[0]["date"])
                target_date = latest_date.replace(year=latest_date.year - 1)
                prior_obs = min(
                    obs[1:],
                    key=lambda o: abs((datetime.date.fromisoformat(o["date"]) - target_date).days),
                    default=None,
                )
                if prior_obs:
                    results[label] = _pct_change(latest_val, _safe_float(prior_obs["value"]))
                else:
                    results[label] = None
            else:
                results[label] = latest_val

        except Exception as e:
            results[label] = None
            results[f"{label}_error"] = str(e)

    # Yield curve spread: 10y - 2y
    if results.get("t10y") and results.get("t2y"):
        results["yield_curve_spread"] = round(results["t10y"] - results["t2y"], 3)

    return results


# ── Events calendar ───────────────────────────────────────────────────────────

def fetch_earnings_calendar(symbols: list[str], days_ahead: int = 14) -> list[dict]:
    """
    Return upcoming earnings dates for the given symbols within the next N days.
    Uses yfinance calendar data.
    """
    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days_ahead)
    events: list[dict] = []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(yahoo_ticker(symbol))
            cal = ticker.calendar
            if cal is None or cal.empty:
                continue

            # yfinance returns a DataFrame with dates as columns
            for col in cal.columns:
                try:
                    d = col.date() if hasattr(col, "date") else datetime.date.fromisoformat(str(col)[:10])
                    if today <= d <= cutoff:
                        events.append({
                            "date": d.isoformat(),
                            "type": "earnings",
                            "symbol": symbol,
                            "days_until": (d - today).days,
                        })
                except (ValueError, AttributeError):
                    continue
        except Exception:
            continue

    events.sort(key=lambda e: e["date"])
    return events


# ── News headlines ────────────────────────────────────────────────────────────

def fetch_headlines(watchlist_symbols: list[str], max_per_feed: int = 20) -> list[dict]:
    """
    Pull recent headlines from RSS feeds and filter to those mentioning
    any symbol in the watchlist or broad market keywords.
    Returns a flat list of relevant headline dicts.
    """
    keywords = set(s.lower() for s in watchlist_symbols)
    keywords.update(["market", "fed", "inflation", "rate", "nasdaq", "s&p", "etf",
                     "recession", "treasury", "earnings", "gdp", "cpi"])

    headlines: list[dict] = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = (title + " " + summary).lower()
                if any(kw in text for kw in keywords):
                    headlines.append({
                        "title": title,
                        "source": feed.feed.get("title", feed_url),
                        "published": entry.get("published", ""),
                        "link": entry.get("link", ""),
                    })
        except Exception:
            continue

    # Deduplicate by title
    seen: set[str] = set()
    unique: list[dict] = []
    for h in headlines:
        if h["title"] not in seen:
            seen.add(h["title"])
            unique.append(h)

    return unique
