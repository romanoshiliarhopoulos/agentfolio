# Portfolio Intelligence Pipeline — Implementation Guide

## What This Document Covers

How to actually build the pipeline described in `overview.md`: what data flows into each agent, how to parse the IBKR export, which free external APIs to connect, and how the file-based communication between agents works in practice.

---

## Understanding the IBKR CSV Export

The daily CSV export (`Portfolio_status.csv`) is a **single file containing multiple sections**, each with its own header row. There is no blank-line separation — sections are identified by their column headers. The parser must handle this explicitly.

### Sections in the Export

| Section header (first column) | What it contains |
|---|---|
| `ClientAccountID, CurrencyPrimary, ReportDate, Cash, Stock, ...` | Daily NAV history — cash, stock, options, bonds, total per day |
| `ClientAccountID, AssetClass, Symbol, Description, TotalUnrealizedPnl, TotalFifoPnl` | Unrealized + total PnL summary per symbol |
| `ClientAccountID, CurrencyPrimary, AssetClass, Symbol, ..., MarkPrice, PositionValue, CostBasisPrice, ...` | Current open positions — one row **per lot**, not per symbol |
| `ClientAccountID, CurrencyPrimary, AssetClass, Symbol, ..., TransactionType, Quantity, TradePrice, ...` | Trade history — every buy/sell |
| `ClientAccountID, CurrencyPrimary, FXRateToBase, AssetClass, Symbol, ..., Amount, Type` | Cash transactions — dividends, withholding tax, deposits/withdrawals |
| `Date/Time, FromCurrency, ToCurrency, Rate` | Daily FX rates |

### Critical Parsing Note: Lots vs Positions

IBKR reports each **purchase lot** as a separate row in the positions section. A single symbol may have multiple rows:

```
AMZN  1.0000 shares  opened 2026-02-05  cost $222.93
AMZN  0.7196 shares  opened 2026-01-05  cost $232.60
AMZN  1.0000 shares  opened 2026-01-05  cost $233.61
AMZN  0.5000 shares  opened 2026-01-05  cost $229.47
```

The parser must **aggregate lots by symbol** to produce a single position record. For each symbol compute:
- Total quantity: sum of all lot quantities
- Weighted average cost basis: `sum(lot_quantity × lot_cost) / total_quantity`
- Total position value: `total_quantity × mark_price`
- Total unrealized PnL: sum across lots (or use the PnL summary section directly)

### Current Holdings (as of CSV)

Your live portfolio for reference:

| Symbol | Description | Total Shares | Mark Price | Position Value | Unrealized PnL |
|--------|-------------|-------------|-----------|----------------|----------------|
| AMZN | Amazon.com Inc | 3.2196 | $200.95 | $647.0 | -$91.67 |
| EQQQ | Invesco Nasdaq-100 | 0.2442 | $567.41 | $138.6 | -$13.13 |
| MSFT | Microsoft Corp | 0.4983 | $358.96 | $178.9 | -$21.46 |
| NVDA | Nvidia Corp | 0.2703 | $165.17 | $44.65 | -$5.84 |
| UBER | Uber Technologies | 1.1550 | $69.91 | $80.8 | -$20.25 |
| VUAA | Vanguard S&P500 (USD) | 1.1366 | $123.32 | $140.2 | -$13.82 |
| EUR | Cash (EUR) | — | — | — | -$2.10 (FX) |

Total NAV: ~$1,956. The portfolio is in early growth stage — all positions entered Jan–Feb 2026.

---

## Python Pre-computation Layer

This layer runs before every agent invocation. It reads the IBKR CSV and calls free external APIs, then writes compact JSON files that agents consume. **No Claude tokens are spent here.**

### Step 1: Parse the IBKR CSV

```python
# parse_ibkr.py
# Detects sections by scanning for known header patterns, not line numbers
# (line numbers shift every time IBKR regenerates the file)

SECTION_SIGNATURES = {
    'nav_history':    ['ClientAccountID', 'CurrencyPrimary', 'ReportDate', 'Cash', 'Stock'],
    'pnl_summary':    ['ClientAccountID', 'AssetClass', 'Symbol', 'Description', 'TotalUnrealizedPnl'],
    'positions':      ['ClientAccountID', 'CurrencyPrimary', 'AssetClass', 'Symbol', 'Description', 'Quantity', 'MarkPrice'],
    'trades':         ['ClientAccountID', 'CurrencyPrimary', 'AssetClass', 'Symbol', 'Description', 'DateTime', 'TransactionType'],
    'cash_txns':      ['ClientAccountID', 'CurrencyPrimary', 'FXRateToBase', 'AssetClass', 'Symbol', 'Description', 'Date/Time', 'SettleDate', 'Amount', 'Type'],
    'fx_rates':       ['Date/Time', 'FromCurrency', 'ToCurrency', 'Rate'],
}
```

Parse strategy: read the file line by line, detect which section you're in by matching column headers, accumulate rows per section, then process each section independently.

### Step 2: Aggregate Positions

After parsing, aggregate the positions section into a clean holdings dict:

```
For each symbol:
  - total_quantity
  - weighted_avg_cost
  - mark_price
  - position_value
  - unrealized_pnl
  - unrealized_pnl_pct
  - first_opened (earliest lot date)
  - lot_count
```

Also extract from the NAV history section:
- Today's total NAV
- NAV 7 days ago, 30 days ago (for performance attribution)
- Cash balance

### Step 3: Fetch Market Data (Free APIs)

For each symbol in holdings + a broader watchlist:

**Yahoo Finance (yfinance — free, no key):**
```
Per holding:    current price, 1d/5d/1mo change %, 52-week range,
                P/E ratio, dividend yield, average volume
Sector ETFs:    SPY, QQQ, IWM, XLF, XLE, XLK, XLV, VNQ
                (broad market pulse, even if not held)
```

**FRED API (free, register for key at fred.stlouisfed.org):**
```
10-year Treasury yield (DGS10)
2-year Treasury yield (DGS2)
Fed Funds Rate (FEDFUNDS)
CPI YoY (CPIAUCSL)
Unemployment rate (UNRATE)
```

**Economic Calendar (free, no key):**
Use the `earnings_calendar` from yfinance for earnings dates of held symbols.
For macro events (FOMC, CPI, jobs), use the `investpy` library or scrape econoday.

**News headlines:**
Use RSS feeds — no API key needed:
- Reuters: `feeds.reuters.com/reuters/businessNews`
- MarketWatch: `feeds.marketwatch.com/marketwatch/topstories`
- Filter to only headlines containing held symbol names or sector keywords

### Step 4: Compute Derived Metrics

All in Python before any agent sees the data:

```
Portfolio level:
  - Total NAV, cash %, invested %
  - 1-week and 1-month return
  - Allocation by symbol (% of total)
  - Implied rebalancing trades to reach equal-weight
  - Total dividend income YTD (from cash transactions section)

Per holding:
  - Unrealized PnL as % of position (not just absolute)
  - Contribution to total portfolio return
  - Days held (from first_opened date)
  - Price vs 52-week high/low (where are we in the range?)

Risk proxies (simple, no paid data needed):
  - Portfolio beta: weighted average of symbol betas from yfinance
  - Concentration: largest single position as % of NAV
  - Equity vs non-equity split
```

### Step 5: Write Context Files

Two output files, one per agent type:

**`data/context/portfolio_snapshot.json`** — consumed by Agents 1, 2, 4 and the daily scout:
```json
{
  "generated_at": "2026-03-31T06:00:00",
  "nav": { "total": 1955.58, "cash": 725.60, "invested": 1229.98 },
  "holdings": [ ... aggregated positions ... ],
  "performance": { "1w_return_pct": ..., "1mo_return_pct": ... },
  "risk": { "portfolio_beta": ..., "largest_position_pct": ... },
  "dividends_ytd": 0.54,
  "market_data": { ... per-symbol price data ... },
  "macro": { "t10y": ..., "t2y": ..., "fed_funds": ..., "cpi_yoy": ... }
}
```

**`data/context/market_research.json`** — consumed by Agent 3 (Market Researcher):
```json
{
  "generated_at": "2026-03-31T06:00:00",
  "sector_etfs": { "SPY": ..., "QQQ": ..., "XLK": ..., ... },
  "macro_environment": { ... FRED data ... },
  "events_calendar": [
    { "date": "2026-04-02", "type": "earnings", "symbol": "...", "impact": "WATCH" },
    { "date": "2026-04-09", "type": "FOMC", "impact": "HIGH" },
    ...
  ],
  "broader_headlines": [ ... filtered RSS headlines ... ]
}
```

---

## Agent Input/Output Contracts

These are the file-based "APIs" between agents — the exact data each agent reads and writes.

### Daily Scout

**Reads:**
- `data/context/portfolio_snapshot.json`
- `data/context/market_research.json`
- Last 3 daily scout logs (for pattern detection across days)

**Writes:**
- `data/scout_logs/YYYY-WNN-day.md` — structured markdown report

**`--max-turns`:** 15

**System prompt mandate:** Interpret the pre-computed data. Do not re-derive numbers. Flag what changed since yesterday and what events are approaching. Write QUIET DAY if thresholds are not met (define thresholds in the prompt: e.g. no position moved >2%, no HIGH-impact event within 3 days, no notable sector momentum).

---

### Agent 1 — Portfolio Analyzer

**Reads:**
- `data/context/portfolio_snapshot.json`
- `data/scout_logs/YYYY-WNN-weekly.md` (full week digest)
- `data/reports/last_week_agent1.json` (prior week's output, for trend detection)

**Writes:**
- `data/weekly/agent1_analysis.json`

**`--max-turns`:** 40

**Output schema:**
```json
{
  "allocation": { "by_symbol": {}, "by_asset_class": {}, "cash_pct": 0 },
  "drift": [ { "symbol": "AMZN", "current_pct": 33.1, "target_pct": 16.7, "drift_pct": 16.4 } ],
  "concentration_flags": [],
  "performance": { "1w": ..., "attribution": [ { "symbol": ..., "contribution_pct": ... } ] },
  "holdings_flags": [ { "symbol": ..., "flag": "ALERT|WATCH|NOTE", "reason": "..." } ],
  "dividends": { "received_ytd": 0.54, "upcoming": [] },
  "trend_vs_prior_week": "..."
}
```

---

### Agent 2 — Risk Assessor

**Reads:**
- `data/context/portfolio_snapshot.json`
- `data/weekly/agent1_analysis.json`
- `data/reports/last_week_agent2.json`

**Writes:**
- `data/weekly/agent2_risk.json`

**`--max-turns`:** 40

**Output schema:**
```json
{
  "risk_score": 6,
  "risk_score_rationale": "...",
  "beta": 1.2,
  "sharpe_estimate": 0.8,
  "stress_scenarios": [
    { "scenario": "market -10%", "estimated_portfolio_impact": "-$195", "pct": "-10%" },
    { "scenario": "market -20%", ... },
    { "scenario": "tech sector -25%", ... }
  ],
  "concentration_risk": "...",
  "correlation_concerns": [],
  "risk_trend_vs_prior_week": "INCREASING|STABLE|DECREASING",
  "appropriate_for_target": true,
  "notes": "..."
}
```

---

### Agent 3 — Market Researcher

**Reads:**
- `data/context/market_research.json`
- `data/weekly/agent1_analysis.json` (to know what's already held — avoids suggesting duplicates)
- High-signal scout logs only: `data/scout_logs/YYYY-WNN-*.md` filtered to non-QUIET-DAY files
- `data/reports/last_week_agent3.json` (prior opportunity candidates — are they still relevant?)

**Writes:**
- `data/weekly/agent3_research.json`

**`--max-turns`:** 35

**Output schema:**
```json
{
  "macro_assessment": "...",
  "opportunity_candidates": [
    {
      "symbol": "VGT",
      "description": "Vanguard Information Technology ETF",
      "rationale": "...",
      "fit_for_portfolio": "...",
      "conviction": "HIGH|MEDIUM|LOW"
    }
  ],
  "events_next_week": [
    { "date": "...", "event": "...", "holdings_affected": [], "impact_assessment": "..." }
  ],
  "prior_candidates_update": [
    { "symbol": "...", "status": "DEVELOPED|STALLED|RESOLVED|STILL_VALID", "notes": "..." }
  ],
  "sectors_to_watch": []
}
```

---

### Agent 4 — Strategy Advisor

**Reads:**
- `data/context/portfolio_snapshot.json` (includes `market_chronicle` field)
- `data/weekly/agent1_analysis.json`
- `data/weekly/agent2_risk.json`
- `data/weekly/agent3_research.json`
- `data/reports/last_week_agent4.json` (prior strategy recommendations — follow-up)
- `config/investor_profile.yaml` (goals, constraints, risk tolerance)

**Writes:**
- `data/weekly/agent4_strategy.json`

**`--max-turns`:** 43

**Output schema:**
```json
{
  "rebalancing": [
    {
      "action": "REDUCE|ADD|HOLD",
      "symbol": "AMZN",
      "rationale": "...",
      "urgency": "NOW|NEXT_4_WEEKS|MONITOR",
      "tax_note": "..."
    }
  ],
  "new_research_candidates": [ { "symbol": "...", "why_now": "...", "source_agent": "agent3|agent1" } ],
  "event_positioning": [ { "event": "...", "recommended_action": "..." } ],
  "top_3_convictions": [ "...", "...", "..." ],
  "prior_recommendations_followup": [ { "prior_rec": "...", "outcome": "..." } ],
  "macro_positioning": "...",
  "chronicle_entry": {
    "week": "2026-W14",
    "macro_regime": "one concise sentence describing the macro environment this week",
    "market_character": "RISK_ON|RISK_OFF|VOLATILE|QUIET|TRENDING_UP|TRENDING_DOWN",
    "significant_events": [
      "Fed held rates at 4.25–4.5%, signalling 2 cuts expected in 2026",
      "CPI came in at 2.4%, slightly above consensus"
    ],
    "structural_shifts": [
      "Only include if something genuinely multi-month changed — e.g. regime change, new trend. Leave empty most weeks."
    ]
  }
}
```

**Chronicle entry rules (long-term portfolio framing):**
- `macro_regime`: one sentence on the macro backdrop — rates direction, growth signals, inflation trend. No weekly price moves.
- `market_character`: a single token from the fixed vocabulary. Describes the week's tone, not a prediction.
- `significant_events`: 2–4 bullets maximum. Only events that could matter **over the next 6–12 months** — Fed decisions, CPI inflection points, major geopolitical shifts, structural sector changes. Exclude: individual earnings beats/misses, single-day moves, short-term noise.
- `structural_shifts`: almost always empty. Reserve for genuinely rare inflections — a new rate cycle beginning, a sector undergoing multi-year change, a macro regime transition. Not "the market was volatile this week."

The agent is explicitly told: *this entry will be read by your future self in 3–6 months when making long-term portfolio decisions. Write only what will still be meaningful then.*

---

### Agent 5 — Report Generator

**Reads:**
- `data/weekly/agent1_analysis.json`
- `data/weekly/agent2_risk.json`
- `data/weekly/agent3_research.json`
- `data/weekly/agent4_strategy.json`
- `config/investor_profile.yaml`

**Writes:**
- `data/reports/YYYY-WNN-report.md`

**`--max-turns`:** 28

**Report sections:**
```
1. Executive Summary (5 sentences)
2. Portfolio Health (table: symbol, value, PnL%, allocation%, drift)
3. This Week in Markets (what mattered for YOUR holdings only)
4. Risk Check (traffic light + score change vs last week)
5. Opportunities & Research Candidates
6. Strategy Corner (rebalancing actions + event alerts)
7. Scout Highlights (best signals from the week's logs)
8. Action Items (numbered, concrete)
9. Next Week's Watch
```

---

### Mid-Cycle Pulse Check Agent

**Reads:**
- `data/reports/last_week_report.md` (Week A full report — the baseline)
- `data/weekly/agent4_strategy.json` (last week's recommendations)
- `data/scout_logs/YYYY-WNN-*.md` (this week's scouts)
- `data/context/portfolio_snapshot.json` (current state)

**Writes:**
- `data/weekly/pulse_check.json`

**`--max-turns`:** 40

**Output schema:**
```json
{
  "escalations": [ { "finding": "...", "reason_cant_wait": "..." } ],
  "tracking": [ { "item": "...", "status": "..." } ],
  "resolved": [ "..." ],
  "allocation_drift_since_week_a": { "AMZN": "+2.1%", ... },
  "verdict": "QUIET|NOTABLE|ESCALATE"
}
```

---

## External Data Connections Summary

| Data type | Source | Key required? | Library |
|---|---|---|---|
| Stock prices, P/E, beta, dividend yield | Yahoo Finance | No | `yfinance` |
| Sector ETF performance | Yahoo Finance | No | `yfinance` |
| Earnings calendar | Yahoo Finance | No | `yfinance` |
| Treasury yields, CPI, unemployment | FRED | Yes (free) | `fredapi` or `requests` |
| Fed Funds Rate | FRED | Yes (free) | `fredapi` |
| Business news headlines | Reuters/MarketWatch RSS | No | `feedparser` |
| FX rates | Already in IBKR CSV | No | — |

Everything needed is available without paid subscriptions. The FRED API key is free to register at `fred.stlouisfed.org`.

---

## Investor Profile Config

Create `config/investor_profile.yaml` — this is injected into Agents 4 and 5 and the mid-cycle report. Keep it short; it's token budget.

```yaml
capital: 25000          # target deployed capital
current_nav: 1956       # update periodically
target_return_pct: 7.5  # annualized midpoint of 5-10% range
risk_tolerance: moderate
horizon: long_term      # 5+ years
style: passive_etf      # prefer ETFs, limit single-stock concentration
max_single_stock_pct: 20
rebalancing_threshold_pct: 5   # act on drift only when position drifts >5% from target
tax_jurisdiction: US    # affects withholding tax notes (EQQQ is Irish-domiciled)
current_holdings:       # updated by the parser, not manually
  - AMZN
  - EQQQ
  - MSFT
  - NVDA
  - UBER
  - VUAA
notes: >
  Portfolio is in early growth phase (~$2k of $25k target deployed).
  EQQQ is the Invesco Nasdaq-100 UCITS ETF (London-listed, USD).
  VUAA is the Vanguard S&P 500 UCITS ETF (London-listed, USD).
  Both are Irish-domiciled — different withholding tax treatment than US ETFs.
```

The Irish-domicile note matters: EQQQ and VUAA are UCITS ETFs listed on Euronext/LSE, not US-listed ETFs. The agents should know this when discussing tax treatment and when the Market Researcher compares them to US equivalents like VOO/QQQ.

---

## File Layout

```
agentfolio/
├── config/
│   └── investor_profile.yaml
├── data/
│   ├── ibkr/
│   │   └── Portfolio_status.csv       ← daily export lands here
│   ├── context/
│   │   ├── portfolio_snapshot.json    ← written by parser before each run
│   │   └── market_research.json       ← written by parser before each run
│   ├── scout_logs/
│   │   ├── 2026-W14-mon.md
│   │   └── ...
│   ├── weekly/
│   │   ├── agent1_analysis.json
│   │   ├── agent2_risk.json
│   │   ├── agent3_research.json
│   │   ├── agent4_strategy.json
│   │   └── pulse_check.json
│   └── reports/
│       ├── 2026-W13-report.md
│       ├── last_week_agent1.json      ← copy of prior week's agent1 output
│       ├── last_week_agent2.json
│       ├── last_week_agent3.json
│       └── last_week_agent4.json
└── mds/
    ├── overview.md
    └── implementation.md
```

The `last_week_*.json` files are how the weekly chain has memory across cycles — at the end of each Sunday pipeline, copy the current `weekly/` outputs into `reports/last_week_*.json` before they're overwritten next week.

`data/chronicle/market_chronicle.json` is the long-term memory layer. Agent 4 writes a `chronicle_entry` field in its output; a Python utility (`chronicle.append_entry()`) extracts it and appends it to this file. The file is capped at 26 entries (~6 months). The last 12 entries are injected as a `market_chronicle` field in both context files so every agent has access to the macro trend history.

---

## Sequencing

**Daily (every morning before markets open):**
1. Run parser (`parse_ibkr.py`) → writes `portfolio_snapshot.json` and `market_research.json`
2. Run daily scout agent → writes scout log

**Sunday Week A (staggered across 5 windows):**
1. Run parser (fresh data)
2. 02:00 AM — Agent 1 (Portfolio Analyzer)
3. 07:00 AM — Agent 2 (Risk Assessor)
4. 12:00 PM — Agent 3 (Market Researcher)
5. 05:00 PM — Agent 4 (Strategy Advisor)
6. 10:00 PM — Agent 5 (Report Generator) → email delivered
7. Copy `weekly/*.json` → `reports/last_week_*.json`

**Sunday Week B (mid-cycle):**
1. Run parser
2. 02:00 AM — Pulse Check Agent
3. 07:00 AM — Mid-cycle Report Generator → shorter email delivered
