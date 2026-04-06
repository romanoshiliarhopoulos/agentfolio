# Deployment Overview

Everything needed to take agentfolio from code to a running overnight system.

---

## Prerequisites

### 1. Claude Code CLI — logged in with Pro account
```bash
claude --version        # should print 2.x.x
claude auth status      # should show authenticated
```
The pipeline uses `claude -p` (non-interactive mode). Pro gives ~470 messages/week across
5-hour rolling windows of 45 messages each. The agents are designed to use ~50% of that budget.

### 2. Poetry virtual environment
```bash
cd /Users/romanos/agentfolio
poetry install          # installs yfinance, feedparser, requests, pandas, python-dotenv
```
The scripts use `.venv/bin/python` directly. Verify:
```bash
.venv/bin/python -c "import yfinance, feedparser, dotenv; print('OK')"
```

### 3. Environment variables — `.env` at repo root
```
IBKR_ACTIVATION_TOKEN=<your token>
IBKR_QUERY_ID=<your flex query id>
FRED_API_KEY=<your key>
```
FRED keys are free: https://fredaccount.stlouisfed.org/apikey

---

## IBKR Data Flow

### Current state: manual CSV
`precompute.py` reads `data/ibkr/Portfolio_status.csv`. This file must be current
before any pipeline run. To update it: log into IBKR → Reports → Activity → Export CSV
and save it to `data/ibkr/Portfolio_status.csv`.

### IBKR Flex API (partially implemented)
`fetch_market_data.py` contains `fetch_ibkr_flex_report()` which fetches the Flex XML
from IBKR's web service using `IBKR_ACTIVATION_TOKEN` and `IBKR_QUERY_ID` from `.env`.

**Status: the XML is fetched but not yet parsed into portfolio positions.**
The function `update_portfolio_snapshot_from_ibkr()` is a TODO — it writes `{}` as a
placeholder. When this is implemented, the scripts will be able to fetch fresh data
automatically before each run without needing a manual CSV download.

**Note:** the `.env` key is `IBKR_QUERY_ID` but `fetch_market_data.py` reads
`IBKR_FLEX_QUERY_ID`. These need to be aligned when the Flex parsing is implemented.

Until then: **manually update the CSV before each first run of the week.**

---

## Schedule

### Bi-weekly cadence
- **Week A** (even ISO week numbers: W02, W04, ...): full pipeline — all 5 agents run on Sunday
- **Week B** (odd ISO week numbers: W01, W03, ...): mid-cycle only — pulse check on Wednesday

Each script checks the current ISO week number and self-exits if it's the wrong week type.
This is determined in the shell scripts by `$(date +%V) % 2`.

To verify which week you're in:
```bash
python3 -c "import datetime; w=datetime.date.today().isocalendar()[1]; print(f'W{w} → Week {\"A\" if w%2==0 else \"B\"}')"
```

### Full schedule

| Job | Script | Day | Time | Week | Rate-limit window |
|-----|--------|-----|------|------|-------------------|
| Scout | `run_scout.sh` | Mon–Sat | 06:00 | Every | Opens at 06:00, 15 turns |
| Agent 1 (Portfolio Analyzer) | `run_agent1.sh` | Sunday | 02:00 | A only | Window 1: 02:00–07:00, 40 turns |
| Agent 2 (Risk Assessor) | `run_agent2.sh` | Sunday | 07:15 | A only | Window 2: 07:15–12:15, 40 turns |
| Agent 3 (Market Researcher) | `run_agent3.sh` | Sunday | 12:30 | A only | Window 3: 12:30–17:30, 35 turns |
| Agent 4 (Strategy Advisor) | `run_agent4.sh` | Sunday | 17:45 | A only | Window 4: 17:45–22:45, 43 turns |
| Agent 5 (Report Generator) | `run_agent5.sh` | Sunday | 23:00 | A only | Window 5: 23:00–04:00, 28 turns |
| Pulse Check | `run_pulse.sh` | Wednesday | 06:00 | B only | ~40 turns |

The 75-minute gaps between Sunday agents (02:00 → 07:15 → 12:30 → 17:45 → 23:00) ensure
the previous 5-hour window has fully expired before the next agent starts.

### Message budget
| Period | Messages used | % of Pro weekly cap (~470) |
|--------|--------------|--------------------------|
| Week A (Sunday) | ~186 (5 agents) | ~40% |
| Week A (Mon–Sat scouts) | ~90 (6 scouts × 15) | ~19% |
| Week B (1 pulse) | ~40 | ~9% |
| Week B (Mon–Sat scouts) | ~90 | ~19% |
| **Total (2-week average)** | **~406/week** | **~43%** |

---

## macOS Sleep — Critical Setup

launchd jobs in `~/Library/LaunchAgents/` run in your user session. They fire correctly
when the display is off but the **CPU is awake**. If the Mac enters actual sleep
(CPU suspended), launchd jobs do NOT fire until the system wakes up again.

**Recommended: prevent CPU sleep when plugged in.**

Open System Settings → Battery → Options (or Energy Saver on older macOS) and enable:
> **"Prevent automatic sleeping on power adapter when the display is off"**

This keeps the CPU running overnight while plugged in. The display still sleeps normally.
Estimated power draw: ~15–25W (MacBook Pro), negligible on mains power.

### Alternative: use `pmset` to schedule wake events
If you prefer the Mac to fully sleep and wake only for each job:

```bash
# Wake every Sunday at 01:45 (15 min before agent1)
sudo pmset repeat wake SU 01:45:00

# For daily scout wake at 05:45
sudo pmset repeat wakeorpoweron MTWRFS 05:45:00
```

Note: `pmset repeat` accepts only one schedule. For multiple times, use `pmset sched`:
```bash
# One-off scheduled wakes (you'd need to re-schedule after each)
sudo pmset sched wake "04/06/2025 01:45:00"
```

`pmset` scheduled wakes are reliable even from deep sleep on Apple Silicon.
The easiest approach remains "prevent automatic sleeping" above.

---

## Installing the launchd Jobs

Copy the plists to `~/Library/LaunchAgents/` and load them:

```bash
PLIST_DIR=/Users/romanos/agentfolio/scripts/launchd
AGENTS_DIR=~/Library/LaunchAgents

# Copy all plists
cp "$PLIST_DIR"/com.agentfolio.*.plist "$AGENTS_DIR/"

# Load them all
for plist in "$AGENTS_DIR"/com.agentfolio.*.plist; do
    launchctl load "$plist"
    echo "Loaded: $plist"
done
```

### Verify they are loaded
```bash
launchctl list | grep agentfolio
```
You should see 7 entries, all with a `-` (dash) in the PID column when not running.

### Unload (to stop all jobs)
```bash
for plist in ~/Library/LaunchAgents/com.agentfolio.*.plist; do
    launchctl unload "$plist"
done
```

### Reload after editing a plist
```bash
launchctl unload ~/Library/LaunchAgents/com.agentfolio.agent1.plist
launchctl load   ~/Library/LaunchAgents/com.agentfolio.agent1.plist
```

---

## First Run Checklist

Run through this before the first automated run:

- [ ] `claude auth status` — shows authenticated with Pro plan
- [ ] `.env` is populated with all three keys
- [ ] `data/ibkr/Portfolio_status.csv` is a recent export from IBKR
- [ ] `poetry install` has been run, `.venv/bin/python` resolves correctly
- [ ] Manual smoke test: `cd /Users/romanos/agentfolio && .venv/bin/python src/precompute.py`
  — should print holdings and write both JSON files without errors
- [ ] Manual scout test: `.venv/bin/python src/agents/scout.py`
  — should write a `.md` file under `data/scout_logs/`
- [ ] launchd plists are loaded: `launchctl list | grep agentfolio` shows 7 entries
- [ ] "Prevent automatic sleeping" is enabled in Battery settings
- [ ] `data/logs/` directory exists (created automatically by scripts on first run)

### Determining your first Week A
Check your current week number:
```bash
python3 -c "import datetime; w=datetime.date.today().isocalendar()[1]; print(f'W{w} → {\"Week A\" if w%2==0 else \"Week B\"}')"
```
If this is Week B, the next Week A Sunday will be in ~1 week. The scouts will still run daily.
If you want to force a full pipeline run immediately, you can run the scripts manually:
```bash
bash scripts/run_agent1.sh   # ignore the week-check exit by editing the script temporarily
```

---

## Log Files

| File | Contents |
|------|----------|
| `data/logs/scout_YYYY-MM-DD.log` | Daily scout output per day |
| `data/logs/weekly_YYYY-WNN.log` | All 5 agents for a Week A run, appended |
| `data/logs/pulse_YYYY-WNN.log` | Pulse check output for a Week B |
| `data/logs/launchd_scout.log` | launchd stdout for scout (duplicate of above) |
| `data/logs/launchd_weekly.log` | launchd stdout for weekly agents |
| `data/logs/launchd_*_err.log` | stderr for each job type — check these first on failure |

To watch a running job live:
```bash
tail -f data/logs/weekly_$(date +%Y-W%V).log
```

To check if today's scout ran:
```bash
cat data/logs/scout_$(date +%Y-%m-%d).log | tail -20
```

---

## Output Files

After a full Week A run, you'll have:

| File | Written by | Contents |
|------|-----------|----------|
| `data/context/portfolio_snapshot.json` | precompute | Holdings, NAV, risk proxies, macro |
| `data/context/market_research.json` | precompute | Sector ETFs, headlines, events |
| `data/scout_logs/YYYY-WNN-mon.md` … | scout | Daily briefing notes |
| `data/weekly/agent1_analysis.json` | agent1 | Allocation, drift, performance |
| `data/weekly/agent2_risk.json` | agent2 | Risk score, stress scenarios |
| `data/weekly/agent3_research.json` | agent3 | Macro regime, opportunity candidates |
| `data/weekly/agent4_strategy.json` | agent4 | Recommendations, verdicts, chronicle entry |
| `data/reports/YYYY-WNN-report.md` | agent5 | Human-readable weekly report |
| `data/reports/last_week_agent*.json` | agent5 | Prior-week continuity for next run |
| `data/chronicle/market_chronicle.json` | agent4 | Rolling 26-week macro memory |
| `data/weekly/pulse_check.json` | pulse | CLEAR/ESCALATE verdict |

The **weekly report** at `data/reports/YYYY-WNN-report.md` is the primary output — open it
Monday morning for the week's portfolio intelligence.

---

## Known Gaps / TODOs

| Gap | Impact | Fix |
|-----|--------|-----|
| IBKR Flex XML parsing is a TODO | Manual CSV update required before each run | Implement `parse_ibkr_flex_xml()` to replace the placeholder in `fetch_market_data.py` |
| `.env` key mismatch: `IBKR_QUERY_ID` vs `IBKR_FLEX_QUERY_ID` | Flex API won't authenticate when parsing is implemented | Align both to `IBKR_QUERY_ID` in `fetch_market_data.py` |
| No alert on scout ESCALATE output | You won't know if pulse check fires an escalation unless you read the log | Add email/notification via macOS `osascript` or `mail` command in `run_pulse.sh` |
| Week A/B starts on a fixed parity | W14 = Week A by default. If you want to start on Week B, flip the modulo check in scripts | Change `ne 0` to `eq 0` (and vice versa) in all scripts |
| Agent 5 archives to `last_week_*.json` but does not delete stale `data/weekly/` files | Old JSON files persist until overwritten next Week A | Not a bug, but adds clutter |

---

## Quick Reference — Manual Runs

```bash
cd /Users/romanos/agentfolio

# Run precompute only
.venv/bin/python src/precompute.py

# Run today's scout
.venv/bin/python src/agents/scout.py

# Run full weekly pipeline manually (respects rate limits — run sequentially with 5h gaps)
.venv/bin/python src/agents/agent1.py
# wait 5+ hours
.venv/bin/python src/agents/agent2.py
# wait 5+ hours
.venv/bin/python src/agents/agent3.py
# ...

# Run pulse check
.venv/bin/python src/agents/agent_pulse.py

# Check what week type today is
python3 -c "import datetime; w=datetime.date.today().isocalendar()[1]; print(f'W{w} → {\"A\" if w%2==0 else \"B\"}')"

# View the latest weekly report
open data/reports/$(ls -t data/reports/*.md | head -1 | xargs basename)
```
