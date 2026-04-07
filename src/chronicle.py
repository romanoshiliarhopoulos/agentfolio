"""
Market Chronicle — rolling long-term memory across weekly pipeline runs.

Agent 4 (Strategy Advisor) writes a chronicle_entry as part of its JSON output.
This module appends that entry to a rolling file capped at MAX_ENTRIES weeks,
and provides a function to load the recent chronicle for injection into context.

The chronicle is intentionally filtered to macro-level, multi-month relevant
events only. Daily price moves, single-week noise, and short-term signals are
excluded. The target reader is an agent analysing a long-term ETF portfolio.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


CHRONICLE_PATH      = Path(__file__).parent.parent / "data" / "chronicle" / "market_chronicle.json"
PERFORMANCE_PATH    = Path(__file__).parent.parent / "data" / "chronicle" / "performance_history.json"
RECOMMENDATIONS_PATH = Path(__file__).parent.parent / "data" / "chronicle" / "recommendations_log.json"
MAX_ENTRIES = 26  # ~6 months of weekly entries


def load() -> list[dict]:
    """Load the full chronicle. Returns empty list if file does not exist yet."""
    if not CHRONICLE_PATH.exists():
        return []
    return json.loads(CHRONICLE_PATH.read_text())


def append_entry(entry: dict) -> None:
    """
    Append a new chronicle entry and trim to MAX_ENTRIES.
    Silently skips if entry is missing required fields.
    """
    required = {"week", "macro_regime", "significant_events", "market_character"}
    if not required.issubset(entry.keys()):
        return

    CHRONICLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = load()

    # Avoid duplicating entries for the same week
    entries = [e for e in entries if e.get("week") != entry["week"]]
    entries.append(entry)

    # Keep most recent MAX_ENTRIES, sorted by week string (ISO week format sorts correctly)
    entries.sort(key=lambda e: e.get("week", ""))
    entries = entries[-MAX_ENTRIES:]

    CHRONICLE_PATH.write_text(json.dumps(entries, indent=2))


def load_for_context(weeks: int = 12) -> list[dict]:
    """
    Return the last N weeks of chronicle entries for injection into agent context.
    12 weeks (~3 months) is the default — enough for trend detection without
    bloating the context payload.
    """
    entries = load()
    return entries[-weeks:]


# ── Performance history ───────────────────────────────────────────────────────

def append_performance_entry(entry: dict) -> None:
    """
    Append a weekly performance snapshot. Expected fields:
      week, nav, cash, invested, risk_score, holdings (symbol→pnl_pct dict)
    """
    required = {"week", "nav"}
    if not required.issubset(entry.keys()):
        return
    PERFORMANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(PERFORMANCE_PATH.read_text()) if PERFORMANCE_PATH.exists() else []
    entries = [e for e in entries if e.get("week") != entry["week"]]
    entries.append(entry)
    entries.sort(key=lambda e: e.get("week", ""))
    entries = entries[-MAX_ENTRIES:]
    PERFORMANCE_PATH.write_text(json.dumps(entries, indent=2))


def load_performance_history(weeks: int = 12) -> list[dict]:
    if not PERFORMANCE_PATH.exists():
        return []
    return json.loads(PERFORMANCE_PATH.read_text())[-weeks:]


def summarise_performance_for_context(weeks: int = 12) -> str:
    """Compact markdown table of weekly NAV + risk score for agent context."""
    entries = load_performance_history(weeks)
    if not entries:
        return ""
    lines = ["## Portfolio Performance History (weekly NAV)\n"]
    lines.append("| Week | NAV | Invested | Cash% | Risk Score |")
    lines.append("|------|-----|----------|-------|------------|")
    for e in entries:
        nav       = e.get("nav", 0)
        invested  = e.get("invested", 0)
        cash      = e.get("cash", 0)
        cash_pct  = round(cash / nav * 100, 1) if nav else 0
        risk      = e.get("risk_score", "n/a")
        lines.append(f"| {e['week']} | ${nav:,.0f} | ${invested:,.0f} | {cash_pct}% | {risk} |")
    return "\n".join(lines)


# ── Recommendation log ────────────────────────────────────────────────────────

def append_recommendations(week: str, recommendations: list[dict]) -> None:
    """Persist this week's agent4 recommendations for multi-week tracking."""
    if not recommendations:
        return
    RECOMMENDATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(RECOMMENDATIONS_PATH.read_text()) if RECOMMENDATIONS_PATH.exists() else []
    log = [e for e in log if e.get("week") != week]
    log.append({"week": week, "recommendations": recommendations})
    log.sort(key=lambda e: e.get("week", ""))
    log = log[-MAX_ENTRIES:]
    RECOMMENDATIONS_PATH.write_text(json.dumps(log, indent=2))


def load_recent_recommendations(weeks: int = 3) -> list[dict]:
    if not RECOMMENDATIONS_PATH.exists():
        return []
    return json.loads(RECOMMENDATIONS_PATH.read_text())[-weeks:]


def summarise_recommendations_for_context(weeks: int = 3) -> str:
    """Show last N weeks of recommendations so agent4 can track open actions."""
    entries = load_recent_recommendations(weeks)
    if not entries:
        return ""
    lines = ["## Recommendation History (last 3 weeks — track open actions)\n"]
    for e in entries:
        lines.append(f"**{e['week']}**")
        for r in e.get("recommendations", []):
            urgency  = r.get("urgency", "?")
            action   = r.get("action", "?")
            horizon  = r.get("time_horizon", "")
            lines.append(f"  [{urgency}] {action}  ({horizon})")
        lines.append("")
    return "\n".join(lines)


def summarise_for_context(weeks: int = 12) -> str:
    """
    Return a compact markdown-formatted summary of the last N chronicle entries,
    suitable for direct injection into an agent's context document.

    Format is intentionally dense to minimise token spend — one entry per line.
    """
    entries = load_for_context(weeks)
    if not entries:
        return "No historical market chronicle available yet."

    lines = ["## Market Chronicle (last ~3 months of weekly macro summaries)\n"]
    lines.append("*Long-term context only — regime-level signals, not weekly noise.*\n")

    for e in entries:
        week   = e.get("week", "?")
        regime = e.get("macro_regime", "")
        char   = e.get("market_character", "")
        events = e.get("significant_events", [])
        shift  = e.get("structural_shifts", [])

        lines.append(f"**{week}** [{char}]")
        lines.append(f"  Macro: {regime}")
        for ev in events:
            lines.append(f"  • {ev}")
        for s in shift:
            lines.append(f"  ► STRUCTURAL: {s}")
        lines.append("")

    return "\n".join(lines)
