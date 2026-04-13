"""
News Filter — Haiku pre-processing pass for Agent 3.

Reads today's headlines plus the last 2 days of archived news,
deduplicates, then calls Claude Haiku to score relevance and
extract themes. Writes a compact digest to:

  data/context/news_digest.json

Runs automatically from agent3.py before context is built.
Uses Haiku (fast, cheap) — not subject to AGENTFOLIO_MODEL override.
"""

import json
import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base import (
    DATA, load_json, run_claude, extract_json, write_json, log, load_prompt,
)

AGENT = "news_filter"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 1  # Single-pass scoring — no tool use needed
NEWS_DIR = DATA / "news"
DIGEST_PATH = DATA / "context" / "news_digest.json"

_SYSTEM_PROMPT_TEMPLATE = load_prompt("news_filter")


def load_recent_headlines(days: int = 3) -> list[dict]:
    """Load and deduplicate headlines from the last N days of archived news files."""
    today = datetime.date.today()
    seen_titles: set[str] = set()
    all_headlines: list[dict] = []

    for offset in range(days):
        date_str = (today - datetime.timedelta(days=offset)).isoformat()
        path = NEWS_DIR / f"{date_str}.json"
        if not path.exists():
            continue
        try:
            headlines = json.loads(path.read_text())
            for h in headlines:
                title = h.get("title", "")
                # Deduplicate on first 60 chars of title (catches rephrased duplicates)
                key = title[:60].lower().strip()
                if key and key not in seen_titles:
                    seen_titles.add(key)
                    all_headlines.append(h)
        except Exception:
            continue

    return all_headlines


def run() -> dict:
    """
    Run the news filter. Returns the digest dict.
    Falls back to empty digest on any failure — agent3 degrades gracefully.
    """
    log(AGENT, "Starting news filter (Haiku)")

    headlines = load_recent_headlines(days=3)
    if not headlines:
        log(AGENT, "No archived headlines found — skipping filter")
        return {}

    # Cap input to 80 headlines (raised from 60 — more sources now)
    headlines = headlines[:80]
    log(AGENT, f"Processing {len(headlines)} deduplicated headlines")

    # Inject current holdings from snapshot (fall back to empty string)
    snapshot_path = DATA / "context" / "portfolio_snapshot.json"
    try:
        snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
        holdings_list = ", ".join(snapshot.get("holdings", {}).keys()) or "no holdings on file"
    except Exception:
        holdings_list = "no holdings on file"
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace("{holdings_list}", holdings_list)

    # Build a compact input list (title + source only — no full summary)
    today_str = datetime.date.today().isoformat()
    compact = [
        {"title": h["title"], "source": h.get("source", "")}
        for h in headlines
    ]
    context = f"Today: {today_str}\n\nHeadlines:\n{json.dumps(compact, indent=2)}"

    try:
        raw = run_claude(system_prompt, context, MAX_TURNS, model=HAIKU_MODEL)
        digest = extract_json(raw)
        digest["_generated_at"] = datetime.datetime.now().isoformat()
        write_json(DIGEST_PATH, digest)
        n = len(digest.get("top_headlines", []))
        themes = digest.get("key_themes", [])
        log(AGENT, f"Digest written — {n} headlines, themes: {themes}")
        return digest
    except Exception as e:
        log(AGENT, f"WARNING: news filter failed ({e}) — agent3 will use raw headlines")
        return {}


if __name__ == "__main__":
    run()
