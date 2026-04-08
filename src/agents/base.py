"""
Shared utilities for all agents.

Each agent:
  1. Builds a context document (plain text/markdown)
  2. Calls `claude -p` with a system prompt via subprocess
  3. Parses and validates the output
  4. Writes result to the appropriate data file
"""

import json
import os
import subprocess
import sys
import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
DATA      = REPO_ROOT / "data"
CONFIG    = REPO_ROOT / "config"
PROMPTS   = REPO_ROOT / "src" / "prompts"


def load_prompt(name: str) -> str:
    """Load a system prompt from src/prompts/<name>.txt."""
    path = PROMPTS / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text().strip()


def load_json(path: Path) -> dict:
    """Load a JSON file. Returns empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_text(path: Path) -> str:
    """Load a text/markdown file. Returns empty string if file doesn't exist."""
    if not path.exists():
        return ""
    return path.read_text()


def load_investor_profile() -> str:
    """Load investor profile YAML as raw text for injection into prompts."""
    return load_text(CONFIG / "investor_profile.yaml")


def current_week() -> str:
    """Return ISO week string e.g. '2026-W14'."""
    today = datetime.date.today()
    return f"{today.year}-W{today.isocalendar()[1]:02d}"


def scout_logs_this_week() -> list[Path]:
    """Return all daily scout log paths for the current week, sorted by date."""
    week = current_week()
    log_dir = DATA / "scout_logs"
    return sorted(log_dir.glob(f"{week}-*.md"))


def high_signal_scout_logs() -> list[Path]:
    """Return scout logs for the current week that are NOT quiet-day logs."""
    logs = []
    for path in scout_logs_this_week():
        content = path.read_text()
        if "QUIET DAY" not in content[:200]:
            logs.append(path)
    return logs


def concat_scout_logs(paths: list[Path]) -> str:
    if not paths:
        return "No scout logs available for this week."
    parts = []
    for p in paths:
        parts.append(f"### {p.stem}\n\n{p.read_text()}")
    return "\n\n---\n\n".join(parts)


def run_claude(system_prompt: str, context: str, max_turns: int,
               model: str | None = None) -> str:
    """
    Call `claude -p` with the given system prompt and context document.
    Returns the raw stdout string.
    Raises RuntimeError on non-zero exit or empty output.

    AGENTFOLIO_MAX_TURNS env var overrides max_turns (used by CLI test mode).
    model: explicit model override (e.g. "claude-haiku-4-5-20251001" for cheap passes).
           Falls back to AGENTFOLIO_MODEL env var, then claude's default.
    """
    override = os.environ.get("AGENTFOLIO_MAX_TURNS")
    if override:
        max_turns = int(override)
    cmd = [
        "claude", "-p", system_prompt,
        "--max-turns", str(max_turns),
        "--output-format", "text",
    ]
    resolved_model = model or os.environ.get("AGENTFOLIO_MODEL")
    if resolved_model:
        cmd += ["--model", resolved_model]
    result = subprocess.run(
        cmd,
        input=context,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}:\n{result.stderr[:500]}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("claude returned empty output")
    return output


def extract_json(raw: str) -> dict:
    """
    Extract the first JSON object from a claude response.
    Claude sometimes wraps JSON in markdown fences — this handles that.
    Raises ValueError if no valid JSON is found.
    """
    # Strip markdown fences if present
    text = raw
    if "```json" in text:
        text = text.split("```json", 1)[1]
        text = text.split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1]
        text = text.split("```", 1)[0]

    text = text.strip()

    # Find the outermost { ... }
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    # Walk to find the matching closing brace
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("Could not find complete JSON object in response")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def log(agent_name: str, msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{agent_name}] {msg}", flush=True)


def send_report_email(subject: str, body: str) -> None:
    """
    Send the weekly report via Gmail using SMTP.
    Reads GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT from env/.env.
    Raises RuntimeError if credentials are missing or send fails.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    # Load .env if not already in environment
    dotenv_path = REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"')

    sender    = os.environ.get("GMAIL_ADDRESS")    or env.get("GMAIL_ADDRESS", "")
    password  = os.environ.get("GMAIL_APP_PASSWORD") or env.get("GMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("GMAIL_RECIPIENT")  or env.get("GMAIL_RECIPIENT", "")

    if not sender or not password or not recipient:
        raise RuntimeError("Email not sent: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, or GMAIL_RECIPIENT missing from .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())
