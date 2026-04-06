"""
Agentfolio CLI — management interface for the portfolio intelligence pipeline.

Usage:
    poetry run python src/cli.py [COMMAND] [OPTIONS]

Commands:
    status          Live dashboard: schedule, last runs, portfolio state
    config          View and edit configuration
    schedule        Manage launchd schedules
    run             Run any agent on demand with custom settings
    prompts         View and edit agent prompts
    logs            View and tail run logs
    integrations    Configure Telegram and Gmail notifications
"""

import os
import sys
import json
import subprocess
import datetime
import importlib
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.syntax import Syntax
from rich.prompt import Prompt, Confirm
from rich.live import Live
from rich.spinner import Spinner
from rich.rule import Rule
from rich import box

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO     = Path(__file__).parent.parent
SRC      = REPO / "src"
CONFIG   = REPO / "config"
DATA     = REPO / "data"
PROMPTS  = SRC / "prompts"
SCRIPTS  = REPO / "scripts"
LAUNCHD  = SCRIPTS / "launchd"
CONTEXT  = DATA / "context"
DOTENV   = REPO / ".env"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

console = Console()

# ── Helpers ───────────────────────────────────────────────────────────────────

AGENTS = {
    "scout":  {"label": "Daily Scout",        "script": SRC / "agents/scout.py",       "turns": 15,  "schedule": "Mon–Sat 06:00"},
    "agent1": {"label": "Portfolio Analyzer", "script": SRC / "agents/agent1.py",      "turns": 40,  "schedule": "Sun 02:00 (Week A)"},
    "agent2": {"label": "Risk Assessor",      "script": SRC / "agents/agent2.py",      "turns": 40,  "schedule": "Sun 07:15 (Week A)"},
    "agent3": {"label": "Market Researcher",  "script": SRC / "agents/agent3.py",      "turns": 35,  "schedule": "Sun 12:00 (Week A)"},
    "agent4": {"label": "Strategy Advisor",   "script": SRC / "agents/agent4.py",      "turns": 43,  "schedule": "Sun 17:00 (Week A)"},
    "agent5": {"label": "Report Generator",   "script": SRC / "agents/agent5.py",      "turns": 28,  "schedule": "Sun 22:00 (Week A)"},
    "pulse":  {"label": "Pulse Check",        "script": SRC / "agents/agent_pulse.py", "turns": 40,  "schedule": "Sun 02:00 (Week B)"},
}

PLIST_LABELS = {
    "scout":  "com.agentfolio.scout",
    "agent1": "com.agentfolio.agent1",
    "agent2": "com.agentfolio.agent2",
    "agent3": "com.agentfolio.agent3",
    "agent4": "com.agentfolio.agent4",
    "agent5": "com.agentfolio.agent5",
    "pulse":  "com.agentfolio.pulse",
}

ENV_KEYS = {
    "IBKR_ACTIVATION_TOKEN": "IBKR Flex API token",
    "IBKR_FLEX_QUERY_ID":    "IBKR Flex Query ID",
    "FRED_API_KEY":          "FRED macro data API key",
    "TELEGRAM_BOT_TOKEN":    "Telegram bot token",
    "TELEGRAM_CHAT_ID":      "Telegram chat/channel ID",
    "GMAIL_ADDRESS":         "Gmail sender address",
    "GMAIL_APP_PASSWORD":    "Gmail app password (not account password)",
    "GMAIL_RECIPIENT":       "Report recipient email address",
    "AGENTFOLIO_MODEL":      "Claude model for all agents (blank = claude-sonnet-4-6)",
    "AGENTFOLIO_MAX_TURNS":  "Global max-turns override for all agents (blank = per-agent defaults)",
}

CLAUDE_MODELS = {
    "claude-sonnet-4-6":          "Sonnet 4.6 — default, best balance",
    "claude-opus-4-6":            "Opus 4.6 — highest quality, most tokens",
    "claude-haiku-4-5-20251001":  "Haiku 4.5 — fastest, cheapest (testing)",
}


def _load_dotenv() -> dict[str, str]:
    """Return current .env contents as a dict."""
    env: dict[str, str] = {}
    if DOTENV.exists():
        for line in DOTENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_dotenv(env: dict[str, str]) -> None:
    """Write dict back to .env, preserving key order from ENV_KEYS first."""
    lines = []
    written = set()
    for key in ENV_KEYS:
        if key in env:
            lines.append(f"{key}={env[key]}")
            written.add(key)
    for key, val in env.items():
        if key not in written:
            lines.append(f"{key}={val}")
    DOTENV.write_text("\n".join(lines) + "\n")


def _is_plist_loaded(label: str) -> bool:
    result = subprocess.run(["launchctl", "list", label], capture_output=True)
    return result.returncode == 0


def _last_log_time(agent: str) -> str | None:
    """Return mtime of most recent log file for this agent."""
    if agent == "scout":
        logs = sorted((DATA / "scout_logs").glob("*.md")) if (DATA / "scout_logs").exists() else []
    else:
        logs = sorted((DATA / "weekly").glob(f"{agent}*.json")) if (DATA / "weekly").exists() else []
    if not logs:
        return None
    mtime = datetime.datetime.fromtimestamp(logs[-1].stat().st_mtime)
    return mtime.strftime("%Y-%m-%d %H:%M")


def _portfolio_nav() -> str:
    snap = CONTEXT / "portfolio_snapshot.json"
    if not snap.exists():
        return "—"
    try:
        data = json.loads(snap.read_text())
        nav = data.get("nav", {}).get("total", 0)
        return f"${nav:,.2f}"
    except Exception:
        return "err"


def _snap_age() -> str:
    snap = CONTEXT / "portfolio_snapshot.json"
    if not snap.exists():
        return "never"
    mtime = datetime.datetime.fromtimestamp(snap.stat().st_mtime)
    delta = datetime.datetime.now() - mtime
    h = int(delta.total_seconds() // 3600)
    m = int((delta.total_seconds() % 3600) // 60)
    if h > 23:
        return f"{delta.days}d ago"
    return f"{h}h {m}m ago"


def _current_week_type() -> str:
    week = datetime.date.today().isocalendar()[1]
    return "A (intensive)" if week % 2 == 0 else "B (mid-cycle)"


def _open_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    subprocess.run([editor, str(path)])


# ── Interactive menu ─────────────────────────────────────────────────────────

# Two-level menu: sections keyed by letter, each with a list of (label, desc, action)
# action is a callable that does the actual work (prompts + _invoke).

def _invoke(args: list[str]) -> None:
    """Invoke a CLI command in-process, output goes straight to terminal."""
    try:
        cli.main(args=args, standalone_mode=False)
    except SystemExit:
        pass
    except Exception as e:
        console.print(f"[red]Error:[/] {e}")


MENU_SECTIONS: "list[tuple[str, str, str, list[tuple[str, str, callable]]]]" = []
# Populated after _invoke is defined (sections reference it via closures).


def _build_menu_sections():
    sections = [
        ("s", "Status", "Dashboard & system health", [
            ("Dashboard", "Pipeline status, NAV, config health",
             lambda: _invoke(["status"])),
        ]),
        ("r", "Reports", "Read agent outputs & scout logs", [
            ("List all outputs", "Overview of reports, agent JSON, scout logs",
             lambda: _invoke(["reports", "list"])),
            ("Final report", "Latest weekly report (Agent 5 markdown)",
             lambda: _week_prompt_then(["reports", "final"])),
            ("Portfolio Analyzer", "Agent 1 — allocation & drift",
             lambda: _invoke(["reports", "agent", "agent1"])),
            ("Risk Assessor", "Agent 2 — risk score & scenarios",
             lambda: _invoke(["reports", "agent", "agent2"])),
            ("Market Researcher", "Agent 3 — opportunities & macro",
             lambda: _invoke(["reports", "agent", "agent3"])),
            ("Strategy Advisor", "Agent 4 — recommendations",
             lambda: _invoke(["reports", "agent", "agent4"])),
            ("Pulse Check", "Mid-cycle pulse — escalations & status",
             lambda: _invoke(["reports", "agent", "pulse"])),
            ("Scout logs", "Daily scout logs for a given week",
             lambda: _scout_logs_prompt()),
        ]),
        ("u", "Run", "Execute agents & pipeline steps", [
            ("Scout", "Daily scout agent",
             lambda: _agent_run_prompt("scout", 15)),
            ("Agent 1 — Portfolio Analyzer", "Deep portfolio analysis",
             lambda: _agent_run_prompt("agent1", 40)),
            ("Agent 2 — Risk Assessor", "Risk scoring & stress tests",
             lambda: _agent_run_prompt("agent2", 40)),
            ("Agent 3 — Market Researcher", "Opportunity & macro scan",
             lambda: _agent_run_prompt("agent3", 35)),
            ("Agent 4 — Strategy Advisor", "Strategy synthesis",
             lambda: _agent_run_prompt("agent4", 43)),
            ("Agent 5 — Report Generator", "Final report markdown",
             lambda: _agent_run_prompt("agent5", 28)),
            ("Full pipeline", "Run all agents in sequence",
             lambda: _pipeline_prompt()),
        ]),
        ("c", "Config", "Environment, keys & investor profile", [
            ("Show config", "All .env values and turn limits",
             lambda: _invoke(["config", "show"])),
            ("Set Claude model", "Choose which Claude model agents use",
             lambda: _invoke(["config", "set-model"])),
            ("Setup wizard", "Interactive key-by-key configuration",
             lambda: _invoke(["config", "wizard"])),
            ("Set a value", "Set one config key directly",
             lambda: _config_set_prompt()),
            ("Edit investor profile", "Open investor_profile.yaml in $EDITOR",
             lambda: _invoke(["config", "edit-profile"])),
        ]),
        ("p", "Prompts", "View & edit agent system prompts", [
            ("List prompts", "All prompt files with sizes",
             lambda: _invoke(["prompts", "list"])),
            *[(f"Edit — {AGENTS[k]['label']}", f"Open {k}.txt in $EDITOR",
               lambda k=k: _invoke(["prompts", "edit", k]))
              for k in AGENTS],
        ]),
        ("h", "Schedule", "Manage launchd jobs", [
            ("Show schedule", "All jobs with loaded/unloaded status",
             lambda: _invoke(["schedule", "list"])),
            ("Install plists", "Copy plists to ~/Library/LaunchAgents",
             lambda: _invoke(["schedule", "install"])),
            ("Enable all", "Load all jobs into launchd",
             lambda: _invoke(["schedule", "enable", "all"])),
            ("Disable all", "Unload all jobs from launchd",
             lambda: _invoke(["schedule", "disable", "all"])),
            ("Enable one job", "Load a specific job",
             lambda: _schedule_toggle_prompt("enable")),
            ("Disable one job", "Unload a specific job",
             lambda: _schedule_toggle_prompt("disable")),
        ]),
        ("l", "Logs", "View run logs", [
            ("Scout logs (this week)", "Today's and prior scout logs",
             lambda: _invoke(["logs", "scout"])),
            ("Weekly agent outputs", "Latest JSON from weekly agents",
             lambda: _invoke(["logs", "weekly"])),
            ("Tail run log", "Last N lines from most recent log file",
             lambda: _tail_prompt()),
        ]),
        ("i", "Integrations", "Telegram & Gmail notifications", [
            ("Configure Telegram", "Set bot token + chat ID",
             lambda: _invoke(["integrations", "telegram"])),
            ("Configure Gmail", "Set Gmail address + app password",
             lambda: _invoke(["integrations", "gmail"])),
            ("Test Telegram", "Send a test message",
             lambda: _invoke(["integrations", "test", "telegram"])),
            ("Test Gmail", "Send a test email",
             lambda: _invoke(["integrations", "test", "gmail"])),
        ]),
    ]
    return sections


# ── Menu action helpers ───────────────────────────────────────────────────────

def _week_prompt_then(base_args: list[str]) -> None:
    week = Prompt.ask("Week [dim](leave blank for latest)[/]", default="")
    _invoke(base_args + ([week] if week else []))


def _scout_logs_prompt() -> None:
    week = Prompt.ask("Week [dim](leave blank for current)[/]", default="")
    active = Confirm.ask("Active days only?", default=False)
    args = ["reports", "scout"] + ([week] if week else [])
    if active:
        args.append("--active-only")
    _invoke(args)


def _precompute_prompt() -> None:
    csv_path = Prompt.ask("IBKR CSV path [dim](leave blank to auto-fetch)[/]", default="")
    args = ["run", "precompute"] + (["--csv", csv_path] if csv_path else [])
    _invoke(args)


def _agent_run_prompt(key: str, default_turns: int) -> None:
    turns = Prompt.ask(f"Max turns [dim](default {default_turns})[/]",
                       default=str(default_turns))
    dry   = Confirm.ask("Dry run?", default=False)
    args  = ["run", key, "--turns", turns]
    if dry:
        args.append("--dry-run")
    _invoke(args)


def _pipeline_prompt() -> None:
    week  = Prompt.ask("Week [dim](a=full / b=mid-cycle)[/]",
                       choices=["a", "b"], default="a")
    turns = Prompt.ask("Max turns per agent [dim](leave blank for defaults)[/]", default="")
    dry   = Confirm.ask("Dry run?", default=False)
    args  = ["run", "pipeline", "--week", week]
    if turns:
        args += ["--turns", turns]
    if dry:
        args.append("--dry-run")
    _invoke(args)


def _config_set_prompt() -> None:
    key = Prompt.ask("Key")
    val = Prompt.ask("Value",
                     password=("TOKEN" in key or "PASSWORD" in key or "KEY" in key))
    _invoke(["config", "set", key, val])


def _tail_prompt() -> None:
    lines = Prompt.ask("Lines to show", default="50")
    _invoke(["logs", "tail", "--lines", lines])


def _schedule_toggle_prompt(action: str) -> None:
    choices = list(AGENTS.keys())
    agent = Prompt.ask("Agent", choices=choices, default="scout")
    _invoke(["schedule", action, agent])


# ── Menu rendering ────────────────────────────────────────────────────────────

def _print_header() -> None:
    console.print()
    console.print(Panel(
        Text.from_markup(
            f"  [bold cyan]agentfolio[/]  [dim]portfolio intelligence pipeline[/]\n"
            f"  NAV [bold green]{_portfolio_nav()}[/]  ·  "
            f"Snapshot [bold]{_snap_age()}[/]  ·  "
            f"Week [bold]{_current_week_type()}[/]"
        ),
        border_style="cyan",
        padding=(0, 1),
    ))
    console.print()


def _print_top_menu(sections) -> None:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("key", style="bold cyan", justify="right", min_width=3)
    table.add_column("section", style="bold", min_width=14)
    table.add_column("desc", style="dim")

    for key, name, desc, _ in sections:
        table.add_row(f"\\[{key}]", name, desc)

    table.add_row("[dim]\\[q][/]", "[dim]quit[/]", "[dim]exit[/]")
    console.print(table)


def _print_section_menu(name: str, items: list) -> None:
    console.print()
    console.rule(f"[bold cyan]{name}[/]")
    console.print()

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("#", style="bold cyan", justify="right", min_width=3)
    table.add_column("action", style="bold", min_width=28)
    table.add_column("desc", style="dim")

    for i, (label, desc, _) in enumerate(items, 1):
        table.add_row(str(i), label, desc)

    table.add_row("[dim]b[/]", "[dim]back[/]", "[dim]return to main menu[/]")
    console.print(table)


def _interactive_menu() -> None:
    sections = _build_menu_sections()
    section_map = {key: (name, desc, items) for key, name, desc, items in sections}

    while True:
        console.clear()
        _print_header()
        _print_top_menu(sections)

        choice = Prompt.ask("\n[bold]›[/]", default="").strip().lower()

        if choice in ("q", "quit"):
            console.clear()
            console.print("[dim]bye.[/]")
            break

        if choice == "":
            continue

        if choice not in section_map:
            continue

        name, _, items = section_map[choice]

        # Section loop
        while True:
            console.clear()
            _print_header()
            _print_section_menu(name, items)
            sub = Prompt.ask("\n[bold]›[/]", default="").strip().lower()

            if sub in ("b", "back", ""):
                break

            if sub in ("q", "quit"):
                console.clear()
                console.print("[dim]bye.[/]")
                return

            try:
                idx = int(sub) - 1
                if not (0 <= idx < len(items)):
                    raise ValueError
            except ValueError:
                continue

            _, _, action = items[idx]
            console.clear()
            action()
            Prompt.ask("\n[dim]Press Enter to return[/]", default="")


# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """
    \b
    ╔═══════════════════════════════════╗
    ║      agentfolio  pipeline         ║
    ╚═══════════════════════════════════╝
    Portfolio intelligence pipeline manager.
    Run with no arguments for interactive mode.
    """
    if ctx.invoked_subcommand is None:
        _interactive_menu()


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Live dashboard: schedule status, last runs, portfolio state."""

    console.print()
    console.rule("[bold cyan]agentfolio — pipeline status[/]")
    console.print()

    # ── Top info bar ────────────────────────────────────────────────────────
    today = datetime.date.today()
    week_type = _current_week_type()
    nav       = _portfolio_nav()
    snap_age  = _snap_age()

    info_table = Table.grid(padding=(0, 4))
    info_table.add_column()
    info_table.add_column()
    info_table.add_column()
    info_table.add_column()
    info_table.add_row(
        f"[dim]Date:[/]  [bold]{today.isoformat()}[/]",
        f"[dim]Week:[/]  [bold]{week_type}[/]",
        f"[dim]NAV:[/]   [bold green]{nav}[/]",
        f"[dim]Snapshot:[/] [bold]{snap_age}[/]",
    )
    console.print(info_table)
    console.print()

    # ── Schedule table ───────────────────────────────────────────────────────
    sched_table = Table(
        title="[bold]Scheduled Jobs[/]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    sched_table.add_column("Agent", style="bold", min_width=8)
    sched_table.add_column("Label", style="dim", min_width=18)
    sched_table.add_column("Schedule")
    sched_table.add_column("launchd", justify="center", min_width=9)
    sched_table.add_column("Last run", min_width=14)

    for key, info in AGENTS.items():
        label   = PLIST_LABELS[key]
        loaded  = _is_plist_loaded(label)
        status_icon = "[green]● loaded[/]" if loaded else "[dim]○ unloaded[/]"
        last    = _last_log_time(key) or "[dim]never[/]"
        sched_table.add_row(
            info["label"],
            label,
            info["schedule"],
            status_icon,
            last,
        )

    console.print(sched_table)
    console.print()

    # ── Config health ────────────────────────────────────────────────────────
    env = _load_dotenv()
    cfg_table = Table(
        title="[bold]Configuration Health[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    cfg_table.add_column("Key")
    cfg_table.add_column("Status", justify="center")

    checks = {
        "IBKR_ACTIVATION_TOKEN": env.get("IBKR_ACTIVATION_TOKEN", ""),
        "IBKR_FLEX_QUERY_ID":    env.get("IBKR_FLEX_QUERY_ID", ""),
        "FRED_API_KEY":          env.get("FRED_API_KEY", ""),
        "TELEGRAM_BOT_TOKEN":    env.get("TELEGRAM_BOT_TOKEN", ""),
        "GMAIL_ADDRESS":         env.get("GMAIL_ADDRESS", ""),
        "claude on PATH":        subprocess.run(["which", "claude"], capture_output=True).returncode == 0,
        ".venv exists":          (REPO / ".venv/bin/python").exists(),
        "investor_profile.yaml": (CONFIG / "investor_profile.yaml").exists(),
        "prompts complete":      all((PROMPTS / f"{k}.txt").exists() for k in AGENTS),
    }

    for name, val in checks.items():
        if isinstance(val, bool):
            ok = val
        else:
            ok = bool(val)
        icon = "[green]✓ ok[/]" if ok else "[red]✗ missing[/]"
        cfg_table.add_row(name, icon)

    console.print(cfg_table)
    console.print()


# ── config ────────────────────────────────────────────────────────────────────

@cli.group()
def config():
    """View and edit configuration (.env, investor profile, turn limits)."""
    pass


@config.command("show")
def config_show():
    """Show all configuration values."""
    env = _load_dotenv()

    table = Table(
        title="[bold].env configuration[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    table.add_column("Key", style="bold")
    table.add_column("Description", style="dim")
    table.add_column("Value")

    for key, desc in ENV_KEYS.items():
        val = env.get(key, "")
        if val and ("TOKEN" in key or "PASSWORD" in key or "KEY" in key):
            display = val[:6] + "…" + val[-4:] if len(val) > 12 else "***"
        elif val:
            display = val
        else:
            display = "[dim italic]not set[/]"
        table.add_row(key, desc, display)

    console.print()
    console.print(table)
    console.print()

    # Model + turn limits
    active_model = env.get("AGENTFOLIO_MODEL", "").strip() or "claude-sonnet-4-6 (default)"
    max_turns_override = env.get("AGENTFOLIO_MAX_TURNS", "").strip() or "—"
    console.print(f"  [bold]Claude model:[/]  {active_model}")
    console.print(f"  [bold]Max turns override:[/]  {max_turns_override}")
    console.print()

    turns_table = Table(
        title="[bold]Agent turn limits (token spend)[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
    )
    turns_table.add_column("Agent")
    turns_table.add_column("Default turns", justify="right")
    turns_table.add_column("Override env var", style="dim")

    for key, info in AGENTS.items():
        turns_table.add_row(
            f"{info['label']} ({key})",
            str(info["turns"]),
            "AGENTFOLIO_MAX_TURNS (global override)",
        )

    console.print(turns_table)
    console.print()


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """Set a configuration value in .env.  Example: config set FRED_API_KEY abc123"""
    env = _load_dotenv()
    env[key] = value
    _write_dotenv(env)
    console.print(f"[green]✓[/] Set [bold]{key}[/]")


@config.command("set-model")
@click.argument("model", required=False, type=click.Choice(list(CLAUDE_MODELS.keys()) + [""]), default=None)
def config_set_model(model):
    """Choose the Claude model used by all agents.  Leave blank to reset to default."""
    if model is None:
        console.print()
        table = Table(box=box.SIMPLE, header_style="bold cyan", border_style="dim")
        table.add_column("Key", style="bold")
        table.add_column("Model ID")
        table.add_column("Description", style="dim")
        for i, (mid, desc) in enumerate(CLAUDE_MODELS.items()):
            table.add_row(str(i + 1), mid, desc)
        console.print(table)
        choice = Prompt.ask(
            "Choose model (1/2/3) or paste a model ID, blank to clear",
            default="",
        )
        if not choice:
            model = ""
        elif choice.isdigit() and 1 <= int(choice) <= len(CLAUDE_MODELS):
            model = list(CLAUDE_MODELS.keys())[int(choice) - 1]
        else:
            model = choice.strip()

    env = _load_dotenv()
    if model:
        env["AGENTFOLIO_MODEL"] = model
        _write_dotenv(env)
        console.print(f"[green]✓[/] Model set to [bold]{model}[/]")
    else:
        env.pop("AGENTFOLIO_MODEL", None)
        env["AGENTFOLIO_MODEL"] = ""
        _write_dotenv(env)
        console.print("[green]✓[/] Model cleared — agents will use [bold]claude-sonnet-4-6[/] (default)")


@config.command("edit-profile")
def config_edit_profile():
    """Open investor_profile.yaml in your editor."""
    path = CONFIG / "investor_profile.yaml"
    if not path.exists():
        console.print(f"[red]Not found:[/] {path}")
        return
    _open_editor(path)
    console.print(f"[green]✓[/] Saved {path.name}")


@config.command("wizard")
def config_wizard():
    """Interactive setup wizard for all configuration values."""
    console.print()
    console.rule("[bold cyan]Configuration Wizard[/]")
    console.print()

    env = _load_dotenv()

    for key, desc in ENV_KEYS.items():
        current = env.get(key, "")
        display = "(set)" if current else "(empty)"
        val = Prompt.ask(
            f"[bold]{key}[/] [dim]{desc}[/] [dim]{display}[/]",
            default=current,
            password=("TOKEN" in key or "PASSWORD" in key or "KEY" in key),
        )
        env[key] = val

    _write_dotenv(env)
    console.print()
    console.print("[green]✓ Configuration saved to .env[/]")


# ── schedule ──────────────────────────────────────────────────────────────────

@cli.group()
def schedule():
    """Manage launchd job schedules."""
    pass


@schedule.command("list")
def schedule_list():
    """Show all jobs and their launchd status."""
    console.print()

    table = Table(
        title="[bold]Launchd Schedule[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    table.add_column("Agent")
    table.add_column("Plist label", style="dim")
    table.add_column("Plist installed", justify="center")
    table.add_column("launchd loaded", justify="center")
    table.add_column("Schedule")

    for key, info in AGENTS.items():
        label    = PLIST_LABELS[key]
        plist_src = LAUNCHD / f"{label}.plist"
        installed = (LAUNCH_AGENTS_DIR / f"{label}.plist").exists()
        loaded    = _is_plist_loaded(label)

        inst_icon   = "[green]✓[/]" if installed else "[red]✗[/]"
        loaded_icon = "[green]● loaded[/]" if loaded else "[dim]○ unloaded[/]"

        table.add_row(
            info["label"],
            label,
            inst_icon,
            loaded_icon,
            info["schedule"],
        )

    console.print(table)
    console.print()


@schedule.command("install")
def schedule_install():
    """Copy all plists to ~/Library/LaunchAgents (does not load them)."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for key in AGENTS:
        label = PLIST_LABELS[key]
        src   = LAUNCHD / f"{label}.plist"
        dst   = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if not src.exists():
            console.print(f"[yellow]⚠[/]  Plist not found: {src.name}")
            continue
        import shutil
        shutil.copy2(src, dst)
        console.print(f"[green]✓[/] Installed {label}")
    console.print()
    console.print("[dim]Run [bold]schedule enable <agent>[/] to activate.[/]")


@schedule.command("enable")
@click.argument("agent", type=click.Choice(list(AGENTS.keys()) + ["all"]))
def schedule_enable(agent):
    """Load a job into launchd (starts scheduling it)."""
    keys = list(AGENTS.keys()) if agent == "all" else [agent]
    for key in keys:
        label = PLIST_LABELS[key]
        plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if not plist.exists():
            console.print(f"[red]✗[/] Not installed: {label}  (run schedule install first)")
            continue
        r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
        if r.returncode == 0:
            console.print(f"[green]✓[/] Loaded {label}")
        else:
            console.print(f"[red]✗[/] Failed: {r.stderr.decode().strip()}")


@schedule.command("disable")
@click.argument("agent", type=click.Choice(list(AGENTS.keys()) + ["all"]))
def schedule_disable(agent):
    """Unload a job from launchd (stops scheduling it)."""
    keys = list(AGENTS.keys()) if agent == "all" else [agent]
    for key in keys:
        label = PLIST_LABELS[key]
        plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
        r = subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        if r.returncode == 0:
            console.print(f"[green]✓[/] Unloaded {label}")
        else:
            console.print(f"[red]✗[/] {r.stderr.decode().strip() or 'already unloaded'}")


# ── run ───────────────────────────────────────────────────────────────────────

@cli.group()
def run():
    """Run any agent or pipeline step on demand."""
    pass


def _run_agent(key: str, turns: int | None, dry_run: bool, csv_path: str | None) -> None:
    info   = AGENTS[key]
    script = info["script"]
    effective_turns = turns if turns is not None else info["turns"]

    console.print()
    console.rule(f"[bold cyan]{info['label']}[/]")

    flags = []
    if turns is not None:
        flags.append(f"[yellow]turns={effective_turns}[/] (overriding default {info['turns']})")
    else:
        flags.append(f"turns={effective_turns} (default)")
    if dry_run:
        flags.append("[yellow]DRY RUN — skipping claude call[/]")
    if csv_path:
        flags.append(f"csv={csv_path}")

    console.print("  " + "  ·  ".join(flags))
    console.print()

    if dry_run:
        console.print("[dim]Dry run: would execute:[/]")
        console.print(f"  [bold]AGENTFOLIO_MAX_TURNS={effective_turns} python {script.relative_to(REPO)}[/]")
        console.print()
        return

    env = os.environ.copy()
    env["AGENTFOLIO_MAX_TURNS"] = str(effective_turns)
    if csv_path:
        env["IBKR_CSV_PATH"] = csv_path
    # Load .env values if not already in environment
    for k, v in _load_dotenv().items():
        env.setdefault(k, v)

    env["PYTHONPATH"] = str(SRC)

    # Always run precompute first to refresh context files
    console.print("[dim]→ Running precompute...[/]")
    pre_result = subprocess.run(
        [sys.executable, str(SRC / "precompute.py")],
        cwd=str(REPO),
        env=env,
    )
    if pre_result.returncode != 0:
        console.print("[red]✗ Precompute failed — aborting agent run[/]")
        return
    console.print("[dim green]✓ Precompute done[/]")
    console.print()

    start = datetime.datetime.now()
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO),
        env=env,
    )
    elapsed = (datetime.datetime.now() - start).seconds

    console.print()
    if result.returncode == 0:
        console.print(f"[green]✓ Finished in {elapsed}s[/]")
    else:
        console.print(f"[red]✗ Exited with code {result.returncode}[/]")


@run.command("precompute")
@click.option("--csv", "csv_path", default=None, help="Override IBKR CSV path")
def run_precompute(csv_path):
    """Run precompute.py — fetches IBKR data and writes context files."""
    console.print()
    console.rule("[bold cyan]Precompute[/]")

    env = os.environ.copy()
    if csv_path:
        env["IBKR_CSV_PATH"] = csv_path
    for k, v in _load_dotenv().items():
        env.setdefault(k, v)

    subprocess.run(
        [sys.executable, str(SRC / "precompute.py")],
        cwd=str(REPO),
        env=env,
    )


@run.command("scout")
@click.option("--turns", default=None, type=int, help="Override max turns (default 15)")
@click.option("--dry-run", is_flag=True, help="Show what would run, don't execute")
@click.option("--csv", "csv_path", default=None, help="Override IBKR CSV path")
def run_scout(turns, dry_run, csv_path):
    """Run the daily scout agent."""
    _run_agent("scout", turns, dry_run, csv_path)


@run.command("agent1")
@click.option("--turns", default=None, type=int, help="Override max turns (default 40)")
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_agent1(turns, dry_run, csv_path):
    """Run Portfolio Analyzer (Agent 1)."""
    _run_agent("agent1", turns, dry_run, csv_path)


@run.command("agent2")
@click.option("--turns", default=None, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_agent2(turns, dry_run, csv_path):
    """Run Risk Assessor (Agent 2)."""
    _run_agent("agent2", turns, dry_run, csv_path)


@run.command("agent3")
@click.option("--turns", default=None, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_agent3(turns, dry_run, csv_path):
    """Run Market Researcher (Agent 3)."""
    _run_agent("agent3", turns, dry_run, csv_path)


@run.command("agent4")
@click.option("--turns", default=None, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_agent4(turns, dry_run, csv_path):
    """Run Strategy Advisor (Agent 4)."""
    _run_agent("agent4", turns, dry_run, csv_path)


@run.command("agent5")
@click.option("--turns", default=None, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_agent5(turns, dry_run, csv_path):
    """Run Report Generator (Agent 5)."""
    _run_agent("agent5", turns, dry_run, csv_path)


@run.command("pulse")
@click.option("--turns", default=None, type=int)
@click.option("--dry-run", is_flag=True)
@click.option("--csv", "csv_path", default=None)
def run_pulse(turns, dry_run, csv_path):
    """Run Pulse Check agent (Week B mid-cycle)."""
    _run_agent("pulse", turns, dry_run, csv_path)


@run.command("pipeline")
@click.option("--turns", default=None, type=int, help="Override turns for ALL agents")
@click.option("--dry-run", is_flag=True)
@click.option("--week", type=click.Choice(["a", "b"]), default="a", show_default=True,
              help="Which pipeline to run (a=full, b=mid-cycle)")
@click.option("--csv", "csv_path", default=None)
def run_pipeline(turns, dry_run, week, csv_path):
    """
    Run the full pipeline sequentially.
    Week A: precompute → agent1 → agent2 → agent3 → agent4 → agent5
    Week B: precompute → pulse
    """
    console.print()
    console.rule(f"[bold cyan]Full Pipeline — Week {week.upper()}[/]")

    steps_a = ["precompute", "agent1", "agent2", "agent3", "agent4", "agent5"]
    steps_b = ["precompute", "pulse"]
    steps   = steps_a if week == "a" else steps_b

    if turns:
        console.print(f"  [yellow]Turn override: {turns} per agent[/]")
    if dry_run:
        console.print("  [yellow]DRY RUN mode[/]")
    console.print(f"  Steps: {' → '.join(steps)}")
    console.print()

    for step in steps:
        if step == "precompute":
            env = os.environ.copy()
            if csv_path:
                env["IBKR_CSV_PATH"] = csv_path
            for k, v in _load_dotenv().items():
                env.setdefault(k, v)
            if not dry_run:
                subprocess.run([sys.executable, str(SRC / "precompute.py")], cwd=str(REPO), env=env)
            else:
                console.print(f"[dim]  dry run: precompute.py[/]")
        else:
            _run_agent(step, turns, dry_run, csv_path)

        if not dry_run:
            console.print()


# ── prompts ───────────────────────────────────────────────────────────────────

@cli.group()
def prompts():
    """View and edit agent system prompts."""
    pass


@prompts.command("list")
def prompts_list():
    """List all prompt files and their sizes."""
    console.print()

    table = Table(
        title="[bold]Agent Prompts[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    table.add_column("Agent")
    table.add_column("File", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("First line", style="dim")

    prompt_keys = list(AGENTS.keys())
    for key in prompt_keys:
        path = PROMPTS / f"{key}.txt"
        if path.exists():
            text  = path.read_text()
            size  = f"{len(text):,} chars"
            first = text.strip().split("\n")[0][:70]
        else:
            size  = "[red]missing[/]"
            first = ""
        table.add_row(
            f"{AGENTS[key]['label']} ({key})",
            path.name,
            size,
            first,
        )

    console.print(table)
    console.print()


@prompts.command("show")
@click.argument("agent", type=click.Choice(list(AGENTS.keys())))
def prompts_show(agent):
    """Print a prompt to the terminal with syntax highlighting."""
    path = PROMPTS / f"{agent}.txt"
    if not path.exists():
        console.print(f"[red]Prompt not found:[/] {path}")
        return
    console.print()
    console.print(Panel(
        Syntax(path.read_text(), "markdown", theme="monokai", word_wrap=True),
        title=f"[bold]{AGENTS[agent]['label']} — {path.name}[/]",
        border_style="cyan",
        expand=False,
    ))


@prompts.command("edit")
@click.argument("agent", type=click.Choice(list(AGENTS.keys())))
def prompts_edit(agent):
    """Open a prompt in your editor ($EDITOR, fallback nano)."""
    path = PROMPTS / f"{agent}.txt"
    if not path.exists():
        console.print(f"[yellow]File does not exist, creating:[/] {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    _open_editor(path)
    size = len(path.read_text())
    console.print(f"[green]✓[/] Saved {path.name} ({size:,} chars)")


# ── JSON → Markdown converters ────────────────────────────────────────────────

def _fmt_pct(v, plus=True) -> str:
    if v is None:
        return "n/a"
    return f"{'+' if plus and v > 0 else ''}{v:.1f}%"


def _agent1_to_md(d: dict) -> str:
    lines = [f"# Portfolio Analyzer — {d.get('week', '?')}\n"]

    alloc = d.get("allocation", {})
    if alloc.get("by_symbol"):
        lines.append("## Allocation\n")
        lines.append("| Symbol | Value | Alloc % | Target % | Drift | Dir |")
        lines.append("|--------|------:|--------:|---------:|------:|-----|")
        for a in alloc["by_symbol"]:
            lines.append(
                f"| {a['symbol']} | ${a.get('position_value', 0):,.2f} "
                f"| {a.get('allocation_pct', 0):.1f}% "
                f"| {a.get('equal_weight_target_pct', 0):.1f}% "
                f"| {_fmt_pct(a.get('drift_pct'))} "
                f"| {a.get('drift_direction', '—')} |"
            )
        lines.append(f"\nCash: **{alloc.get('cash_pct', 0):.1f}%** · "
                     f"Invested: **{alloc.get('total_invested_pct', 0):.1f}%** · "
                     f"Concentration: **{alloc.get('concentration_flag', '—')}**\n")

    perf = d.get("performance", {})
    if perf:
        lines.append("## Performance\n")
        lines.append(f"- **1w return:** {_fmt_pct(perf.get('1w_return_pct'))}")
        lines.append(f"- **1mo return:** {_fmt_pct(perf.get('1mo_return_pct'))}\n")
        for attr in perf.get("attribution", []):
            lines.append(f"**{attr['symbol']}:** {attr.get('contribution_note', '')}\n")

    flags = d.get("holdings_flags", [])
    if flags:
        lines.append("## Holdings Flags\n")
        for f in flags:
            lines.append(f"- **[{f.get('flag', '?')}] {f.get('symbol', '?')}:** {f.get('reason', '')}")
        lines.append("")

    drift = d.get("drift_summary")
    if drift:
        lines.append(f"## Drift Summary\n{drift}\n")

    wvp = d.get("week_vs_prior")
    if wvp:
        lines.append(f"## Week vs Prior\n{wvp}\n")

    obs = d.get("notable_observations", [])
    if obs:
        lines.append("## Notable Observations\n")
        for o in obs:
            lines.append(f"- {o}")
        lines.append("")

    return "\n".join(lines)


def _agent2_to_md(d: dict) -> str:
    lines = [f"# Risk Assessor — {d.get('week', '?')}\n"]

    score = d.get("risk_score")
    trend = d.get("risk_trend_vs_prior_week", "—")
    appropriate = d.get("appropriate_for_target")
    lines.append(f"**Risk Score:** {score}/10  ·  **Trend:** {trend}  ·  "
                 f"**Appropriate for target:** {'Yes' if appropriate else 'No'}\n")
    rationale = d.get("risk_score_rationale")
    if rationale:
        lines.append(f"{rationale}\n")

    note = d.get("appropriateness_note")
    if note:
        lines.append(f"**Appropriateness:** {note}\n")

    beta = d.get("beta", {})
    if beta:
        lines.append(f"## Beta\n**Portfolio beta:** {beta.get('portfolio', '—')}\n\n{beta.get('interpretation', '')}\n")

    conc = d.get("concentration_risk", {})
    if conc:
        lines.append(f"## Concentration Risk\n**Level:** {conc.get('level', '—')}\n\n{conc.get('detail', '')}\n")

    for corr in d.get("correlation_concerns", []):
        holdings = ", ".join(corr.get("holdings", []))
        lines.append(f"## Correlation Concern: {holdings}\n{corr.get('concern', '')}\n")

    curr = d.get("currency_risk", {})
    if curr:
        lines.append("## Currency Risk\n")
        lines.append(curr.get("eurusd_exposure_note", ""))
        scenario = curr.get("scenario_eur_strengthens_5pct")
        if scenario:
            lines.append(f"\n**EUR +5% scenario:** {scenario}\n")

    scenarios = d.get("stress_scenarios", [])
    if scenarios:
        lines.append("## Stress Scenarios\n")
        lines.append("| Scenario | Impact USD | New NAV |")
        lines.append("|----------|----------:|--------:|")
        for s in scenarios:
            lines.append(
                f"| {s.get('scenario', '?')} "
                f"| ${s.get('estimated_impact_usd', 0):+,.0f} "
                f"| ${s.get('estimated_new_nav', 0):,.0f} |"
            )
        lines.append("")

    key_risks = d.get("key_risks", [])
    if key_risks:
        lines.append("## Key Risks\n")
        for r in key_risks:
            lines.append(f"- {r}")
        lines.append("")

    mitigating = d.get("mitigating_factors", [])
    if mitigating:
        lines.append("## Mitigating Factors\n")
        for m in mitigating:
            lines.append(f"- {m}")
        lines.append("")

    notes = d.get("notes")
    if notes:
        lines.append(f"## Notes\n{notes}\n")

    return "\n".join(lines)


def _agent3_to_md(d: dict) -> str:
    lines = [f"# Market Researcher — {d.get('week', '?')}\n"]

    macro = d.get("macro_assessment", {})
    if macro:
        lines.append(f"## Macro Assessment\n**Regime:** {macro.get('regime', '—')}\n")
        lines.append(macro.get("regime_rationale", ""))
        themes = macro.get("dominant_themes", [])
        if themes:
            lines.append("\n**Dominant themes:**")
            for t in themes:
                lines.append(f"- {t}")
        relevance = macro.get("long_term_relevance")
        if relevance:
            lines.append(f"\n**Long-term relevance:** {relevance}")
        lines.append("")

    candidates = d.get("opportunity_candidates", [])
    if candidates:
        lines.append("## Opportunity Candidates\n")
        for c in candidates:
            lines.append(f"### {c.get('ucits_instrument', '?')} — {c.get('instrument_name', '')}")
            lines.append(f"**Theme:** {c.get('theme', '')}  "
                         f"**Fit:** {c.get('fit_for_portfolio', '—')}  "
                         f"**Conviction:** {c.get('conviction', '—')}\n")
            lines.append(c.get("rationale", ""))
            risks = c.get("risks")
            if risks:
                lines.append(f"\n**Risks:** {risks}\n")

    prior = d.get("prior_candidates_update", [])
    if prior:
        lines.append("## Prior Candidates Update\n")
        for p in prior:
            lines.append(f"**{p.get('ucits_instrument', '?')}** ({p.get('theme', '')}) — "
                         f"Status: **{p.get('status', '—')}**\n{p.get('update', '')}\n")

    events = d.get("events_next_week", [])
    if events:
        lines.append("## Events Next Week\n")
        for e in events:
            lines.append(f"- {e}")
        lines.append("")

    sectors = d.get("sectors_to_watch", [])
    if sectors:
        lines.append("## Sectors to Watch\n")
        for s in sectors:
            lines.append(f"- {s}")
        lines.append("")

    themes = d.get("scout_log_themes", [])
    if themes:
        lines.append("## Scout Log Themes\n")
        for t in themes:
            lines.append(f"- {t}")
        lines.append("")

    notes = d.get("notes")
    if notes:
        lines.append(f"## Notes\n{notes}\n")

    return "\n".join(lines)


def _agent4_to_md(d: dict) -> str:
    lines = [f"# Strategy Advisor — {d.get('week', '?')}\n"]

    assessment = d.get("strategic_assessment")
    if assessment:
        lines.append(f"## Strategic Assessment\n{assessment}\n")

    recs = d.get("recommendations", [])
    if recs:
        lines.append("## Recommendations\n")
        for r in recs:
            urgency = r.get("urgency", "")
            urgency_fmt = f"[{urgency}]" if urgency else ""
            lines.append(f"### P{r.get('priority', '?')} {urgency_fmt} — {r.get('action', '')}\n")
            rationale = r.get("rationale")
            if rationale:
                lines.append(f"{rationale}\n")
            impact = r.get("expected_impact")
            if impact:
                lines.append(f"**Expected impact:** {impact}\n")

    cash = d.get("cash_deployment")
    if cash:
        lines.append(f"## Cash Deployment\n{cash}\n")

    verdicts = d.get("research_candidate_verdicts", [])
    if verdicts:
        lines.append("## Research Candidate Verdicts\n")
        for v in verdicts:
            lines.append(f"**{v.get('instrument', '?')}** — {v.get('verdict', '—')}: {v.get('rationale', '')}\n")

    outlook = d.get("multi_week_outlook")
    if outlook:
        lines.append(f"## Multi-week Outlook\n{outlook}\n")

    posture = d.get("risk_posture")
    posture_note = d.get("risk_posture_rationale")
    if posture:
        lines.append(f"## Risk Posture\n**{posture}**")
        if posture_note:
            lines.append(f"\n{posture_note}\n")

    notes = d.get("notes")
    if notes:
        lines.append(f"\n## Notes\n{notes}\n")

    return "\n".join(lines)


def _pulse_to_md(d: dict) -> str:
    lines = [f"# Pulse Check — {d.get('week', '?')} ({d.get('check_date', '')})\n"]

    verdict = d.get("verdict", "—")
    lines.append(f"**Verdict: {verdict}**\n")
    rationale = d.get("verdict_rationale")
    if rationale:
        lines.append(f"{rationale}\n")

    escalations = d.get("escalations", [])
    if escalations:
        lines.append("## Escalations\n")
        for e in escalations:
            sev = e.get("severity", "?")
            cat = e.get("category", "?")
            lines.append(f"### [{sev}] {cat}\n{e.get('description', '')}\n")
            action = e.get("suggested_action")
            if action:
                lines.append(f"**Action:** {action}\n")

    statuses = d.get("week_a_recommendations_status", [])
    if statuses:
        lines.append("## Week A Recommendations Status\n")
        for s in statuses:
            lines.append(f"**{s.get('action', '?')}**\nStatus: **{s.get('status', '—')}**\n{s.get('notes', '')}\n")

    drift = d.get("allocation_drift_since_week_a", {})
    if drift:
        material = drift.get("any_material_drift")
        detail = drift.get("details", "")
        lines.append(f"## Allocation Drift\n**Material drift:** {'Yes' if material else 'No'}\n{detail}\n")

    tracking = d.get("tracking", {})
    if tracking:
        lines.append("## Tracking\n")
        for k, v in tracking.items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    resolved = d.get("resolved", [])
    if resolved:
        lines.append("## Resolved\n")
        for r in resolved:
            lines.append(f"- **{r.get('item', '?')}:** {r.get('resolution', '')}")
        lines.append("")

    notes = d.get("notes")
    if notes:
        lines.append(f"## Notes\n{notes}\n")

    return "\n".join(lines)


_AGENT_MD_CONVERTERS = {
    "agent1": _agent1_to_md,
    "agent2": _agent2_to_md,
    "agent3": _agent3_to_md,
    "agent4": _agent4_to_md,
    "pulse":  _pulse_to_md,
}


def _agent_json_to_md(agent_key: str, data: dict) -> str:
    converter = _AGENT_MD_CONVERTERS.get(agent_key)
    if converter:
        return converter(data)
    # fallback: pretty JSON
    return json.dumps(data, indent=2)


# ── reports ───────────────────────────────────────────────────────────────────

WEEKLY_AGENTS = {
    "agent1": ("Portfolio Analyzer",  DATA / "weekly" / "agent1_analysis.json"),
    "agent2": ("Risk Assessor",       DATA / "weekly" / "agent2_risk.json"),
    "agent3": ("Market Researcher",   DATA / "weekly" / "agent3_research.json"),
    "agent4": ("Strategy Advisor",    DATA / "weekly" / "agent4_strategy.json"),
    "pulse":  ("Pulse Check",         DATA / "weekly" / "pulse_check.json"),
}


@cli.group()
def reports():
    """Read outputs from all agents — final reports, intermediate JSON, scout logs."""
    pass


@reports.command("list")
def reports_list():
    """List all available reports and agent outputs with their dates."""
    console.print()

    # ── Final reports ────────────────────────────────────────────────────────
    reports_dir = DATA / "reports"
    final_reports = sorted(reports_dir.glob("*-report.md"), reverse=True) if reports_dir.exists() else []

    if final_reports:
        t = Table(
            title="[bold]Final Weekly Reports[/]",
            box=box.ROUNDED, header_style="bold cyan", border_style="dim", expand=True,
        )
        t.add_column("#", justify="right", style="dim", min_width=3)
        t.add_column("File")
        t.add_column("Date", min_width=14)
        t.add_column("Size", justify="right")
        for i, p in enumerate(final_reports, 1):
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            t.add_row(str(i), p.name, mtime, f"{p.stat().st_size:,} B")
        console.print(t)
        console.print()

    # ── Weekly agent outputs ─────────────────────────────────────────────────
    t2 = Table(
        title="[bold]Weekly Agent Outputs[/]",
        box=box.ROUNDED, header_style="bold cyan", border_style="dim", expand=True,
    )
    t2.add_column("Agent")
    t2.add_column("File", style="dim")
    t2.add_column("Last written", min_width=14)
    t2.add_column("Size", justify="right")
    for key, (label, path) in WEEKLY_AGENTS.items():
        if path.exists():
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            size  = f"{path.stat().st_size:,} B"
        else:
            mtime = "[dim]never[/]"
            size  = "—"
        t2.add_row(f"{label} ({key})", path.name, mtime, size)
    console.print(t2)
    console.print()

    # ── Scout logs ───────────────────────────────────────────────────────────
    scout_dir = DATA / "scout_logs"
    if scout_dir.exists():
        logs = sorted(scout_dir.glob("*.md"), reverse=True)[:10]
        if logs:
            t3 = Table(
                title="[bold]Scout Logs (last 10)[/]",
                box=box.ROUNDED, header_style="bold cyan", border_style="dim", expand=True,
            )
            t3.add_column("File")
            t3.add_column("Date", min_width=14)
            t3.add_column("Quiet?", justify="center")
            t3.add_column("Size", justify="right")
            for p in logs:
                mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                quiet = "[dim]QUIET[/]" if "QUIET DAY" in p.read_text()[:200] else "[green]active[/]"
                t3.add_row(p.name, mtime, quiet, f"{p.stat().st_size:,} B")
            console.print(t3)
            console.print()


@reports.command("final")
@click.argument("week", required=False, default=None, metavar="WEEK")
def reports_final(week):
    """Read the final weekly report. WEEK: ISO week e.g. 2026-W14 (default: most recent)."""
    """Read the final weekly report (Agent 5 output)."""
    reports_dir = DATA / "reports"
    if not reports_dir.exists():
        console.print("[dim]No reports directory found.[/]")
        return

    if week:
        path = reports_dir / f"{week}-report.md"
        if not path.exists():
            console.print(f"[red]Not found:[/] {path.name}")
            return
    else:
        candidates = sorted(reports_dir.glob("*-report.md"), reverse=True)
        if not candidates:
            console.print("[dim]No final reports yet.[/]")
            return
        path = candidates[0]

    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    console.print()
    console.print(Panel(
        Syntax(path.read_text(), "markdown", theme="monokai", word_wrap=True),
        title=f"[bold]{path.stem}[/]  [dim]{mtime}[/]",
        border_style="cyan",
    ))


@reports.command("agent")
@click.argument("agent", type=click.Choice(list(WEEKLY_AGENTS.keys())))
@click.option("--raw", is_flag=True, help="Show raw JSON instead of formatted markdown.")
def reports_agent(agent, raw):
    """Read the latest output from a specific weekly agent (rendered as markdown)."""
    label, path = WEEKLY_AGENTS[agent]
    if not path.exists():
        console.print(f"[dim]No output yet for {label}.[/]")
        return

    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        data = {}

    if raw or not data:
        text = json.dumps(data, indent=2) if data else path.read_text()
        lang = "json"
    else:
        text = _agent_json_to_md(agent, data)
        lang = "markdown"

    console.print()
    console.print(Panel(
        Syntax(text, lang, theme="monokai", word_wrap=True),
        title=f"[bold]{label}[/]  [dim]{mtime}[/]",
        border_style="cyan",
    ))


@reports.command("scout")
@click.argument("week", required=False, default=None, metavar="WEEK")
@click.option("--active-only", is_flag=True, help="Skip quiet-day logs.")
def reports_scout(week, active_only):
    """Read scout logs. WEEK: ISO week e.g. 2026-W14 (default: current)."""
    if week is None:
        today = datetime.date.today()
        week  = f"{today.year}-W{today.isocalendar()[1]:02d}"

    scout_dir = DATA / "scout_logs"
    paths = sorted(scout_dir.glob(f"{week}-*.md")) if scout_dir.exists() else []

    if not paths:
        console.print(f"[dim]No scout logs for {week}.[/]")
        return

    for path in paths:
        text = path.read_text()
        is_quiet = "QUIET DAY" in text[:200]
        if active_only and is_quiet:
            continue
        label = f"[dim]QUIET[/] {path.stem}" if is_quiet else f"[bold]{path.stem}[/]"
        console.print()
        console.print(Panel(
            Syntax(text, "markdown", theme="monokai", word_wrap=True),
            title=label,
            border_style="dim cyan" if is_quiet else "cyan",
        ))


@reports.command("all")
def reports_all():
    """Show every available output — final report, all agent JSON, all scout logs."""
    # Final report
    reports_dir = DATA / "reports"
    finals = sorted(reports_dir.glob("*-report.md"), reverse=True) if reports_dir.exists() else []
    if finals:
        path  = finals[0]
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print()
        console.print(Panel(
            Syntax(path.read_text(), "markdown", theme="monokai", word_wrap=True),
            title=f"[bold]Final Report — {path.stem}[/]  [dim]{mtime}[/]",
            border_style="cyan",
        ))

    # Weekly agents
    for key, (label, path) in WEEKLY_AGENTS.items():
        if not path.exists():
            continue
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        try:
            data = json.loads(path.read_text())
            text = _agent_json_to_md(key, data)
            lang = "markdown"
        except json.JSONDecodeError:
            text = path.read_text()
            lang = "markdown"
        console.print()
        console.print(Panel(
            Syntax(text, lang, theme="monokai", word_wrap=True),
            title=f"[bold]{label}[/]  [dim]{mtime}[/]",
            border_style="dim cyan",
        ))

    # Scout logs (current week)
    today = datetime.date.today()
    week  = f"{today.year}-W{today.isocalendar()[1]:02d}"
    scout_dir = DATA / "scout_logs"
    for path in sorted(scout_dir.glob(f"{week}-*.md")) if scout_dir.exists() else []:
        console.print()
        console.print(Panel(
            Syntax(path.read_text(), "markdown", theme="monokai", word_wrap=True),
            title=f"[bold]{path.stem}[/]",
            border_style="dim",
        ))


# ── logs ──────────────────────────────────────────────────────────────────────

@cli.group()
def logs():
    """View and tail pipeline run logs."""
    pass


@logs.command("scout")
@click.option("--week", default=None, help="ISO week e.g. 2026-W14 (default: current)")
def logs_scout(week):
    """Show this week's scout logs."""
    if week is None:
        today = datetime.date.today()
        week  = f"{today.year}-W{today.isocalendar()[1]:02d}"

    log_dir = DATA / "scout_logs"
    paths   = sorted(log_dir.glob(f"{week}-*.md")) if log_dir.exists() else []

    if not paths:
        console.print(f"[dim]No scout logs found for {week}[/]")
        return

    for path in paths:
        console.print()
        console.print(Panel(
            path.read_text(),
            title=f"[bold]{path.stem}[/]",
            border_style="dim cyan",
        ))


@logs.command("weekly")
def logs_weekly():
    """Show the most recent weekly agent outputs."""
    weekly_dir = DATA / "weekly"
    if not weekly_dir.exists():
        console.print("[dim]No weekly outputs yet.[/]")
        return

    for key in ["agent1", "agent2", "agent3", "agent4", "agent5"]:
        path = weekly_dir / f"{key}_analysis.json"
        if not path.exists():
            continue
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        console.print()
        console.print(Panel(
            Syntax(path.read_text()[:2000], "json", theme="monokai"),
            title=f"[bold]{AGENTS[key]['label']}[/] [dim]{mtime}[/]",
            border_style="dim cyan",
            expand=False,
        ))


@logs.command("tail")
@click.option("--lines", default=50, show_default=True)
def logs_tail(lines):
    """Tail the most recent run log file."""
    log_dir = DATA / "logs"
    if not log_dir.exists() or not list(log_dir.glob("*.log")):
        console.print("[dim]No log files found in data/logs/[/]")
        return

    latest = max(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    console.print(f"[dim]Showing last {lines} lines of [bold]{latest.name}[/][/]")
    console.print()

    all_lines = latest.read_text().splitlines()
    for line in all_lines[-lines:]:
        if "ERROR" in line or "✗" in line:
            console.print(f"[red]{line}[/]")
        elif "WARN" in line or "⚠" in line:
            console.print(f"[yellow]{line}[/]")
        elif "✓" in line or "Finished" in line or "Wrote" in line:
            console.print(f"[green]{line}[/]")
        else:
            console.print(line)


# ── integrations ─────────────────────────────────────────────────────────────

@cli.group()
def integrations():
    """Configure notification integrations (Telegram, Gmail)."""
    pass


@integrations.command("telegram")
def integrations_telegram():
    """Configure Telegram bot for report notifications."""
    console.print()
    console.rule("[bold cyan]Telegram Integration[/]")
    console.print()
    console.print(Panel(
        "[bold]How to set up:[/]\n"
        "1. Message [link=https://t.me/BotFather]@BotFather[/link] on Telegram → /newbot\n"
        "2. Copy the [bold]bot token[/] it gives you\n"
        "3. Start a chat with your bot (or add it to a channel)\n"
        "4. Get your chat ID: message [link=https://t.me/userinfobot]@userinfobot[/link] or use\n"
        "   [dim]curl https://api.telegram.org/bot<TOKEN>/getUpdates[/]\n"
        "5. Enter both values below",
        border_style="dim",
    ))
    console.print()

    env = _load_dotenv()

    token = Prompt.ask(
        "Bot token",
        default=env.get("TELEGRAM_BOT_TOKEN", ""),
        password=True,
    )
    chat_id = Prompt.ask(
        "Chat ID",
        default=env.get("TELEGRAM_CHAT_ID", ""),
    )

    env["TELEGRAM_BOT_TOKEN"] = token
    env["TELEGRAM_CHAT_ID"]   = chat_id
    _write_dotenv(env)
    console.print()
    console.print("[green]✓ Saved. Run [bold]integrations test telegram[/] to verify.[/]")


@integrations.command("gmail")
def integrations_gmail():
    """Configure Gmail for report delivery."""
    console.print()
    console.rule("[bold cyan]Gmail Integration[/]")
    console.print()
    console.print(Panel(
        "[bold]How to set up:[/]\n"
        "1. Enable 2-Factor Authentication on your Google account\n"
        "2. Go to [dim]myaccount.google.com → Security → App passwords[/]\n"
        "3. Create an app password for 'Mail'\n"
        "4. Use that 16-char app password below (NOT your Google password)",
        border_style="dim",
    ))
    console.print()

    env = _load_dotenv()

    address   = Prompt.ask("Gmail address", default=env.get("GMAIL_ADDRESS", ""))
    password  = Prompt.ask("App password",  default=env.get("GMAIL_APP_PASSWORD", ""), password=True)
    recipient = Prompt.ask("Recipient email", default=env.get("GMAIL_RECIPIENT", address))

    env["GMAIL_ADDRESS"]      = address
    env["GMAIL_APP_PASSWORD"] = password
    env["GMAIL_RECIPIENT"]    = recipient
    _write_dotenv(env)
    console.print()
    console.print("[green]✓ Saved. Run [bold]integrations test gmail[/] to verify.[/]")


@integrations.command("test")
@click.argument("service", type=click.Choice(["telegram", "gmail"]))
def integrations_test(service):
    """Send a test notification to verify the integration works."""
    env = _load_dotenv()

    if service == "telegram":
        token   = env.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = env.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            console.print("[red]✗[/] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
            return
        try:
            import requests
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "✅ agentfolio test message — integration working."},
                timeout=10,
            )
            r.raise_for_status()
            console.print("[green]✓ Telegram message sent.[/]")
        except Exception as e:
            console.print(f"[red]✗ Failed:[/] {e}")

    elif service == "gmail":
        address   = env.get("GMAIL_ADDRESS", "")
        password  = env.get("GMAIL_APP_PASSWORD", "")
        recipient = env.get("GMAIL_RECIPIENT", "")
        if not address or not password or not recipient:
            console.print("[red]✗[/] Gmail credentials not fully configured.")
            return
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText("agentfolio test message — integration working.")
            msg["Subject"] = "[agentfolio] test"
            msg["From"]    = address
            msg["To"]      = recipient
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(address, password)
                smtp.send_message(msg)
            console.print(f"[green]✓ Email sent to {recipient}.[/]")
        except Exception as e:
            console.print(f"[red]✗ Failed:[/] {e}")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.path.insert(0, str(SRC))
    cli()
