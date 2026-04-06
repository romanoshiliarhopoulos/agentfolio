# Portfolio Intelligence Pipeline — Architecture Design

## Overview

An automated investment portfolio management system built on Claude Code CLI (`claude -p`). The system operates as a **pyramid intelligence architecture**: lightweight daily scout agents accumulate market intelligence throughout the week, which a heavy weekly analyst synthesizes into a comprehensive portfolio report.

**Portfolio context:** ~$25,000 capital · ETFs and safer assets · Target 5–10% annualized returns · Long-term compounding horizon · IBKR brokerage

---

## Architecture

The system runs on a **bi-weekly alternating cadence**. Every other Sunday is an intensive deep-analysis pipeline (Week A). The Sunday in between is a lighter mid-cycle check-in (Week B). Daily scouts run every week regardless.

```
  WEEK A (Intensive)                    WEEK B (Mid-cycle)
  ─────────────────────────             ──────────────────────────
  Analyzer → Risk →                     Pulse Check → Report
  Market Research →
  Strategy → Report
       ↑ reads                                ↑ reads
  Daily Scouts (Mon–Sat)                Daily Scouts (Mon–Sat)
  (full depth, 15 msg each)            (full depth, 15 msg each)
```

The pipeline is **entirely file-based** — agents communicate through structured documents, not live calls to each other.

---

## Token Design Principles

Token management is the central design constraint of this system. Every structural decision exists to minimize wasteful token spend while preserving analytical quality.

### Pre-computation Before Claude

The single biggest token-saving decision: **Python handles all computation before any agent is invoked.** Claude never sees raw CSV exports, full price histories, or unprocessed API responses. Instead it receives a pre-digested context document of under 2,000 tokens.

What Python computes before handing off:
- Portfolio allocation breakdown (% per holding, sector, asset class)
- Daily and weekly price changes per holding
- Risk metrics: beta, Sharpe estimate, max drawdown, correlation matrix
- Distance from target allocation
- Headline aggregation (filtered to relevant instruments)

Claude's job is interpretation, not arithmetic. This separation is non-negotiable.

### Context Payload Design

Each agent receives a **context document** — a structured markdown file assembled before the agent is invoked. The context document is the unit of inter-agent communication.

**Daily scout context (~1,500 tokens):**
```
Portfolio snapshot (summary)
Today's price movements (pre-computed deltas, not raw prices)
Sector performance (index-level, not individual holdings)
News headlines (watchlist holdings + broader market themes)
Macro indicators (key rates, major index moves)
Events calendar (upcoming earnings, FOMC, economic releases — next 5 days)
```

**Weekly portfolio context (~3,000 tokens):**
```
Full portfolio holdings with computed metrics
Weekly scout digest (concatenated daily logs)
Weekly market summary
Macro environment snapshot
Previous week's recommendations (for follow-up)
Investor profile (goals, risk tolerance, constraints)
```

**Weekly market research context (~2,000 tokens, separate payload):**
```
Broader sector performance (beyond current holdings)
Macro theme summary (rate environment, growth signals, risk-on/off)
High-signal scout logs only (non-quiet days, see below)
Events calendar for the coming week
```

The portfolio context and market research context are kept separate because they serve different agents with different mandates. Merging them would unnecessarily bloat the context for agents that don't need external market data.

### Token Budget

Each agent session is designed to meaningfully exhaust its 5-hour window. Sessions are staggered so each starts with a fresh 45-message budget (see Window Strategy below).

**Week A — Intensive Sunday:**

| Component              | Messages |
|------------------------|----------|
| Daily scouts (6×)      | ~90      |
| Portfolio Analyzer     | ~40      |
| Risk Assessor          | ~40      |
| Market Researcher      | ~35      |
| Strategy Advisor       | ~43      |
| Report Generator       | ~28      |
| On-demand briefs       | ~20      |
| **Week A Total**       | **~296** |

**Week B — Mid-cycle Sunday:**

| Component              | Messages |
|------------------------|----------|
| Daily scouts (6×)      | ~90      |
| Pulse Check            | ~40      |
| Mid-cycle Report       | ~20      |
| On-demand briefs       | ~20      |
| **Week B Total**       | **~170** |

**Fortnightly average: ~233 messages/week** — targeting ~50% of the Pro plan's weekly cap, leaving the remainder for personal chat use. Adjust daily scout depth first if you need to reclaim budget — scouts at 15 messages × 6 days are the single largest recurring line item across both week types.

### Window Strategy

The 5-hour rolling window is a per-account limit shared across all active sessions. Running agents in parallel does not give each its own budget — they split the window. To give each agent full budget, the pipeline must **stagger sessions with gaps between them**.

Since all pipeline runs are overnight, timing gaps are free. The Sunday pipeline is designed to span ~20 hours across 5 windows:

```
Sun 2:00 AM  →  Agent 1 (Portfolio Analyzer)    ~40 messages
[window resets at 7:00 AM]
Sun 7:00 AM  →  Agent 2 (Risk Assessor)         ~40 messages
[window resets at 12:00 PM]
Sun 12:00 PM →  Agent 3 (Market Researcher)     ~35 messages
[window resets at 5:00 PM]
Sun 5:00 PM  →  Agent 4 (Strategy Advisor)      ~43 messages
[window resets at 10:00 PM]
Sun 10:00 PM →  Agent 5 (Report Generator)      ~28 messages
→  Report delivered by ~11:00 PM Sunday
```

Daily scouts run each morning in their own isolated session and never compete with each other or with the weekly pipeline.

---

## Agentic Flow

### Daily Loop

Every morning, the system runs a scout agent with a budget of ~15 messages. It goes through several passes rather than producing a single-shot output.

**Scout output format:**
```
## Scout Report — [DATE]
### Alerts           (portfolio holdings needing attention)
### Upcoming Events  (earnings, FOMC, economic releases in next 5 days)
### Opportunity Signals (notable trends, sectors, or instruments outside current holdings)
### Watch Items      (monitor, not urgent)
### Market Notes     (context for weekly)
### Headlines        (1-sentence summaries, relevant holdings only)
```

**Scout analysis passes (~15 messages):**

1. **Initial read** — ingest today's pre-computed data, price movements, events calendar, and broader headlines
2. **Cross-reference** — read the last 3 days' scout logs to identify developing patterns; a single-day anomaly is less significant than the same signal appearing 3 days running
3. **Events assessment** — evaluate each upcoming event on the calendar: does this earnings release, Fed meeting, CPI print, or jobs report materially affect any current holding or plausible opportunity? Rate each event as IMPACT / WATCH / IGNORE
4. **Opportunity scan** — review broader market headlines beyond the watchlist; identify sectors or instruments with momentum or macro tailwinds relevant to a long-term ETF investor (2–4 signals max, with brief rationale)
5. **Draft and refine** — produce the structured output, then review it for signal-to-noise ratio before writing the final log

**Upcoming Events** section: Python pre-fetches the economic calendar so the scout interprets relevance rather than discovering facts. The scout's job is to rate each event's impact, not list them.

The **"quiet day" rule** still applies at the end of pass 5: if no alerts, no IMPACT-rated events, and no opportunity signals survive the review, the scout writes "QUIET DAY — [2 sentences]." Quiet days are excluded from the Market Researcher's input.

Six daily scout logs accumulate across the week. On Sunday, these are concatenated into a **weekly digest** — the memory layer for portfolio-focused weekly agents. A separate filtered set (non-quiet days only) is passed to the Market Researcher.

---

### Weekly Chain

The weekly pipeline is a **sequential 5-agent chain**. Each agent receives a scoped context package and produces a structured document passed downstream.

```
Portfolio Context          Market Research Context
      │                           │
      ▼                           ▼
┌──────────────┐          ┌──────────────┐
│  Agent 1     │          │  Agent 3     │  ← runs in parallel with 1+2
│  Portfolio   │          │  Market      │
│  Analyzer    │          │  Researcher  │
│              │          │              │
│  input:      │          │  input:      │
│  — holdings  │          │  — broader   │
│    metrics   │          │    market    │
│    digest    │          │    context   │
│  output:     │          │  — high-     │
│  — allocation│          │    signal    │
│    drift     │          │    scout     │
│    flags     │          │    logs only │
└──────┬───────┘          │  — Agent 1   │
       │                  │    output    │
       ▼                  │  output:     │
┌──────────────┐          │  — opportu-  │
│  Agent 2     │          │    nity      │
│  Risk        │          │    candidates│
│  Assessor    │          │  — upcoming  │
│              │          │    events    │
│  input:      │          │    & risks   │
│  — portfolio │          └──────┬───────┘
│    context   │                 │
│  + Agent 1   │                 │
│  output:     │                 │
│  — risk score│                 │
│    scenarios │                 │
│    concerns  │                 │
└──────┬───────┘                 │
       │                         │
       └──────────┬──────────────┘
                  ▼
        ┌──────────────┐
        │  Agent 4     │  Strategy Advisor
        │              │
        │  input:      │  — Portfolio context
        │              │  — Agent 1 (allocation state)
        │              │  — Agent 2 (risk profile)
        │              │  — Agent 3 (opportunities + events)
        │  output:     │  — Rebalancing considerations
        │              │  — New research candidates
        │              │  — Positions to watch
        │              │  — Event-driven alerts for coming week
        │              │  — Top 3 highest-conviction observations
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │  Agent 5     │  Report Generator
        │  input:      │  — All four agent outputs + investor profile
        │  output:     │  — Final report (email-ready markdown)
        └──────────────┘
```

**Agents run sequentially, each in its own staggered window** (see Window Strategy). The previous parallel design is dropped — parallel sessions share the same window budget, which defeats the goal of giving each agent full capacity.

---

**Agent 1 — Portfolio Analyzer (~40 messages)**

Multi-pass deep analysis of the current portfolio:
1. Initial holdings review — allocation breakdown, sector weights, asset class distribution
2. Read and compare previous 4 weeks' reports — identify trends in allocation drift, not just point-in-time state
3. Deep dive on flagged holdings — any position with a scout ALERT or significant weekly movement gets individual attention
4. Attribution analysis — what actually drove portfolio returns this week, and why
5. Drift quantification — how far each position has moved from target allocation, and the implied rebalancing trade
6. Output review pass — prune any observations that don't meet the "actionable or notable" bar

**Agent 2 — Risk Assessor (~40 messages)**

Multi-scenario risk analysis building on Agent 1's allocation picture:
1. Baseline risk metrics — beta, Sharpe estimate, max drawdown (from pre-computed Python data)
2. Stress test scenarios — model portfolio behavior under: -10% market, -20% market, rate spike, sector crash for each major holding sector
3. Correlation analysis — identify pairs of holdings that are more correlated than they appear; hidden concentration risk
4. Liquidity and volatility review — any holding that would be difficult to exit in a stress scenario
5. Historical comparison — compare current risk score to the prior 4 weeks; is risk trending up or down?
6. Comparison to target — is the current risk profile appropriate for a 5–10% annual return goal, or is the portfolio taking too much/too little risk?

**Agent 3 — Market Researcher (~35 messages)**

Outward-looking opportunity and event analysis, running in its own window after Agents 1 and 2:
1. Sector landscape — review all major sector ETFs for momentum, flow signals, and macro alignment; compare against what the portfolio already holds
2. Macro theme analysis — assess current macro environment (rates, growth, inflation, dollar strength) and which asset classes it favors
3. Opportunity identification — generate candidate instruments or sectors worth researching, filtered strictly to what makes sense for a long-term, low-turnover ETF portfolio
4. Event impact assessment — for each high-impact event on next week's calendar, assess which holdings or candidate instruments are most exposed
5. Prior opportunities review — revisit last week's opportunity candidates: did they develop, stall, or become irrelevant?
6. Output filtering — rank candidates by conviction, remove anything that requires active trading or high risk tolerance

**Agent 4 — Strategy Advisor (~43 messages)**

The synthesis agent — highest budget, highest value. First to see the complete picture:
1. Full context read — ingest Agent 1 (portfolio state), Agent 2 (risk profile), Agent 3 (opportunities + events) outputs in full
2. Rebalancing analysis — for each drift flag from Agent 1, assess whether it warrants action: timing, tax implications, transaction costs
3. Opportunity fit assessment — for each candidate from Agent 3, evaluate fit against current portfolio: does it reduce concentration, improve diversification, align with risk target?
4. Event positioning — for high-impact upcoming events, assess whether any pre-event portfolio adjustments are worth considering
5. Priority ranking — force-rank all observations into a top-3 highest-conviction list; everything else is secondary
6. Follow-up on prior recommendations — what happened to last week's strategy suggestions?
7. Review and challenge pass — the agent argues against its own recommendations; prunes anything with weak rationale

**Agent 5 — Report Generator (~28 messages)**

Synthesis into a readable final report:
1. Draft all sections from agent outputs
2. Review each section: is the signal clear? Is anything redundant across sections?
3. Refine executive summary to accurately reflect the week's most important findings
4. Final consistency check — are the risk section and strategy section coherent with each other?

---

**Output format:** Agents 1–4 produce structured JSON. Agent 5 produces markdown. JSON keeps inter-agent payloads compact and precisely scoped.

**Context accumulation:** Agent 4 has the largest context of the week. This is intentional — the cost is justified because strategy advice with full context produces materially better output than advice with partial context.

---

### Mid-Cycle Pipeline (Week B)

The mid-cycle pipeline runs on the Sunday between intensive weeks. It does not repeat the deep analysis — the full report from Week A is still valid. Instead it asks: *what has changed since last week, and does anything require attention before the next intensive?*

Two agents, two windows:

```
Sun 2:00 AM  →  Pulse Check Agent   ~40 messages
[window resets at 7:00 AM]
Sun 7:00 AM  →  Mid-cycle Report    ~20 messages
→  Shorter report delivered by ~8:00 AM Sunday
```

**Pulse Check Agent (~40 messages)**

Explicitly constrained to delta analysis — it receives last week's full report alongside this week's scout digest, and its mandate is to identify only what has *changed*:

1. Read Week A's Strategy Advisor output and final report as baseline
2. Review the week's scout logs for any ALERTs or IMPACT-rated events
3. Drift check — has allocation moved materially since last week's rebalancing recommendations?
4. Opportunity follow-up — did any of last week's research candidates develop meaningfully?
5. Event review — were there any surprises from events that were flagged as WATCH last week?
6. Verdict pass — for each observation, classify as: ESCALATE (warrants attention before Week A), TRACKING (noted, no action), or RESOLVED

**What "ESCALATE" means:** if the Pulse Check finds something that genuinely can't wait two weeks — a sharp allocation drift, an unexpected market event that changes the risk picture — it flags it explicitly. The Report Generator turns these into a short alert-style email rather than a full report.

**Mid-cycle Report Generator (~20 messages)**

Produces a short report — not a full weekly report. Structure:

```
## Mid-Cycle Check — [DATE]
### What Changed (vs last week)
### Escalations (if any)
### Tracking (developments to monitor)
### Resolved (items from last week now closed)
### Upcoming Events (next 7 days)
```

If nothing escalated, the report is 3–4 short paragraphs. This is intentional — a quiet mid-cycle week should produce a quiet report.

**What the mid-cycle does NOT do:** re-run portfolio analysis, risk scoring, or market research. Those are computationally expensive and the outputs from Week A remain valid unless the Pulse Check escalates something that invalidates them.

---

### On-Demand Brief

Triggered manually. Budget of ~10 messages — enough for a thorough but focused briefing:
1. Read today's scout log and the last 3 days for context
2. Pull the latest portfolio snapshot
3. Identify the 2–3 most pressing things to know right now
4. Write a concise ~300-word briefing

No structured output, no downstream consumers. Exists purely for the user's convenience.

---

## Agent Prompt Design Philosophy

Each agent has a **single, narrow job**. Persona focus prevents agents from wandering into territory that belongs to a downstream agent — the analyzer doesn't give strategy advice, the risk assessor doesn't write recommendations.

Key prompt constraints across all agents:
- **Multi-pass by design:** agents are explicitly instructed to draft, then review, then refine — not produce single-shot output. This is what fills the window meaningfully rather than padding with filler
- **Self-challenge pass:** analytical agents (1–4) are instructed to argue against their own outputs in a final pass; anything that doesn't survive scrutiny is pruned before being passed downstream
- **Scope enforcement:** each agent is explicitly told what is *not* its job — the Market Researcher does not assess current portfolio risk; the Risk Assessor does not suggest new positions
- **Investor framing:** all analysis is filtered through the $25k / 5–10% / long-term / ETF lens — the Market Researcher specifically should not surface opportunities that require active trading or high risk tolerance
- **No financial advice:** Agent 4 frames everything as "considerations for your research." The user makes all trading decisions.
- **Events as first-class data:** the scout treats the economic calendar with the same priority as price movements — a known earnings release or Fed decision is more actionable than a price delta

---

## Memory and State

The system has **no persistent runtime state** — all memory is written to files.

| Memory type                 | Written by           | Read by                            | Horizon     |
|-----------------------------|----------------------|------------------------------------|-------------|
| Daily scout log             | Scout agent          | Weekly agents + Market Researcher  | 1 week      |
| High-signal scout logs      | Scout agent          | Market Researcher (filtered)       | 1 week      |
| Weekly digest               | Shell (concat)       | Agents 1, 2, 4                     | 1 week      |
| Agent 1–4 outputs           | Weekly agents        | Next agent(s) in chain             | 1 week      |
| Final report                | Agent 5              | User (email)                       | —           |
| Last recommendations        | Agent 4 output       | Next week's portfolio context      | 1–2 weeks   |
| Last opportunity candidates | Agent 3 output       | Next week's market research context| 1–2 weeks   |
| Market chronicle            | Agent 4 + chronicle.py | All agents via context files     | 6 months    |

**Market chronicle — long-term memory:** Agent 4 writes a `chronicle_entry` as part of its weekly output. A utility extracts it and appends it to `data/chronicle/market_chronicle.json`, capped at 26 entries (~6 months). The last 12 weeks are injected into both context files as a compact markdown summary every time `precompute.py` runs.

The chronicle is filtered strictly to **macro-regime level signals** — rate cycles, inflation inflection points, structural sector shifts. Weekly price moves and short-term noise are excluded. The framing the agent is given: *write only what will still be meaningful to a long-term investor reading this in 6 months.*

**Why this matters for a long-term portfolio:** A single week's analysis happens in a vacuum without the chronicle. With it, an agent can detect that rates have been falling for 4 months, that a particular sector has been trending for two quarters, or that a recommendation made in January is now playing out — without re-reading 6 months of raw data.

---

## Disclaimers

1. **Research assistance, not financial advice.** All trading decisions remain with the user.
2. **Token estimates are approximate.** Monitor actual usage in the first few runs and adjust pipeline frequency accordingly.
3. **The 5–10% target is achievable with diversified ETFs** without AI assistance. The value here is discipline, systematic monitoring, and surfacing information you might miss — not alpha generation.
