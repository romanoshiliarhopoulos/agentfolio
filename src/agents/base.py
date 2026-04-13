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

    # Strip the max-turns notice claude appends to stdout, then use whatever content exists.
    # If there's nothing left after stripping, raise so callers don't save/email garbage.
    lines = output.splitlines()
    content_lines = [l for l in lines if "Reached max turns" not in l]
    clean = "\n".join(content_lines).strip()
    if not clean:
        raise RuntimeError(f"claude reached max turns with no usable output (max_turns={max_turns})")
    return clean


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


_EMAIL_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          font-size: 15px; line-height: 1.7; color: #1a1a1a;
          max-width: 760px; margin: 40px auto; padding: 0 28px;
          background: #ffffff; }}
  h1 {{ font-size: 1.7em; border-bottom: 3px solid #0055cc; padding-bottom: 10px;
        color: #003399; margin-top: 0; }}
  h2 {{ font-size: 1.2em; margin-top: 2em; margin-bottom: 0.4em;
        color: #0044aa; border-left: 4px solid #0055cc; padding-left: 10px;
        background: #f0f5ff; padding: 6px 10px; border-radius: 0 4px 4px 0; }}
  h3 {{ font-size: 1.05em; margin-top: 1.4em; color: #333; }}
  h4 {{ font-size: 0.95em; margin: 1.4em 0 0.4em; color: #0044aa;
        text-transform: uppercase; letter-spacing: 0.04em; }}
  p {{ margin: 0.6em 0; }}
  strong {{ color: #111; }}
  ul, ol {{ padding-left: 1.5em; margin: 0.5em 0; }}
  li {{ margin: 5px 0; }}
  blockquote {{ border-left: 4px solid #0055cc; margin: 12px 0;
                padding: 8px 16px; color: #444; background: #f8f9ff;
                border-radius: 0 4px 4px 0; }}
  code {{ background: #f0f0f0; border-radius: 3px; padding: 2px 6px;
          font-size: 0.88em; font-family: 'SF Mono', Consolas, monospace;
          color: #c7254e; }}
  pre {{ background: #f6f8fa; border: 1px solid #e0e0e0; border-radius: 6px;
         padding: 14px 16px; overflow-x: auto; font-size: 0.88em; }}
  pre code {{ background: none; color: inherit; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.2em 0;
           font-size: 0.93em; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  thead {{ background: #0055cc; color: #ffffff; }}
  thead th {{ padding: 10px 14px; text-align: left; font-weight: 600; }}
  tbody tr:nth-child(even) {{ background: #f5f8ff; }}
  tbody tr:hover {{ background: #eef2ff; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #e0e8ff; }}
  hr {{ border: none; border-top: 1px solid #dde; margin: 28px 0; }}
  a {{ color: #0055cc; }}
  .footer {{ margin-top: 48px; font-size: 0.78em; color: #aaa;
             border-top: 1px solid #eee; padding-top: 14px; text-align: center; }}
  .agent-reports {{ margin-top: 40px; border-top: 2px solid #dde; padding-top: 24px; }}
  .agent-reports > h2 {{ margin-top: 0; }}
  details {{ margin: 12px 0; border: 1px solid #dde; border-radius: 6px; overflow: hidden; }}
  summary {{ padding: 10px 14px; background: #f0f5ff; cursor: pointer;
             font-weight: 600; color: #003399; font-size: 0.95em;
             list-style: none; display: flex; align-items: center; }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: "▶"; font-size: 0.7em; margin-right: 8px;
                     transition: transform 0.2s; color: #0055cc; }}
  details[open] summary::before {{ transform: rotate(90deg); }}
  details pre {{ margin: 0; border-radius: 0; border: none;
                 border-top: 1px solid #dde; max-height: 500px; overflow-y: auto; }}
  .agent-body {{ padding: 16px 18px; background: #fff; }}
  .stats-grid {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 16px; }}
  .stat-card {{ background: #f5f8ff; border: 1px solid #dde; border-radius: 6px;
                padding: 10px 16px; min-width: 90px; }}
  .stat-label {{ font-size: 0.75em; color: #666; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.03em; }}
  .stat-value {{ font-size: 1.15em; font-weight: 700; }}
  .pos {{ color: #1a7f37; }}
  .neg {{ color: #c0392b; }}
  .neu {{ color: #555; }}
  .badge {{ display: inline-block; padding: 2px 9px; border-radius: 10px;
            font-size: 0.80em; font-weight: 700; letter-spacing: 0.02em; color: #fff; }}
  .badge-red {{ background: #c0392b; }}
  .badge-orange {{ background: #d68910; }}
  .badge-green {{ background: #1a7f37; }}
  .badge-blue {{ background: #0055cc; }}
  .badge-grey {{ background: #888; color: #fff; }}
  .rec-box {{ border-left: 4px solid #0055cc; background: #f0f5ff;
              padding: 12px 16px; border-radius: 0 6px 6px 0; margin: 10px 0; }}
  .rec-box .rec-action {{ font-size: 1.05em; font-weight: 700; margin-bottom: 6px; }}
  .rec-box .rec-rationale {{ color: #333; font-size: 0.93em; }}
  .json-raw {{ margin-top: 12px; }}
  .json-raw summary {{ background: #f8f8f8; color: #666; font-size: 0.82em;
                       font-weight: 500; padding: 6px 12px; }}
  .json-raw summary::before {{ color: #aaa; }}
  .conviction-bar {{ display: inline-block; width: 80px; height: 8px; background: #eee;
                     border-radius: 4px; vertical-align: middle; margin-left: 6px; }}
  .conviction-fill {{ height: 100%; border-radius: 4px; background: #0055cc; }}
  .obs-list {{ list-style: none; padding: 0; }}
  .obs-list li {{ padding: 8px 12px; margin: 6px 0; background: #f8f9ff;
                  border-left: 3px solid #0055cc; border-radius: 0 4px 4px 0; }}
  .obs-list li.top {{ border-left-color: #1a7f37; background: #f0fff4; font-weight: 600; }}
</style>
</head>
<body>
{content}
{agent_reports_html}
<div class="footer">Agentfolio · generated {date}</div>
</body>
</html>"""


def _he(s: object) -> str:
    """HTML-escape a value for safe inline insertion."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _badge(text: str, color: str = "blue") -> str:
    return f'<span class="badge badge-{color}">{_he(text)}</span>'


def _stat_card(label: str, value: object, css_class: str = "neu") -> str:
    return (f'<div class="stat-card">'
            f'<div class="stat-label">{_he(label)}</div>'
            f'<div class="stat-value {css_class}">{_he(value)}</div>'
            f'</div>')


def _pct_class(v: object) -> str:
    try:
        return "pos" if float(v) > 0 else "neg" if float(v) < 0 else "neu"
    except (TypeError, ValueError):
        return "neu"


def _fmt_pct(v: object) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v) if v is not None else "n/a"


def _fmt_usd(v: object) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v) if v is not None else "n/a"


def _render_agent1_html(data: dict) -> str:
    parts: list[str] = []

    # --- Allocation table ---
    alloc = data.get("allocation", {})
    by_symbol = alloc.get("by_symbol", [])
    if by_symbol:
        parts.append("<h4>Allocation</h4>")
        parts.append('<table><thead><tr>'
                     '<th>Symbol</th><th>Value</th><th>Weight</th>'
                     '<th>Target</th><th>Drift</th><th>Direction</th>'
                     '</tr></thead><tbody>')
        for row in by_symbol:
            direction = row.get("drift_direction", "")
            d_class = "neg" if direction == "OVER" else "pos" if direction == "UNDER" else "neu"
            d_badge = _badge(direction, "red" if direction == "OVER" else "green" if direction == "UNDER" else "grey")
            drift = row.get("drift_pct", 0)
            parts.append(
                f'<tr>'
                f'<td><strong>{_he(row.get("symbol",""))}</strong></td>'
                f'<td>{_fmt_usd(row.get("position_value"))}</td>'
                f'<td>{row.get("allocation_pct", 0):.1f}%</td>'
                f'<td>{row.get("equal_weight_target_pct", 0):.1f}%</td>'
                f'<td class="{d_class}">{_fmt_pct(drift)}</td>'
                f'<td>{d_badge}</td>'
                f'</tr>'
            )
        parts.append('</tbody></table>')

        row2_parts = []
        cash_pct = alloc.get("cash_pct")
        if cash_pct is not None:
            row2_parts.append(f'Cash: <strong>{cash_pct:.1f}%</strong>')
        conc = alloc.get("concentration_flag", "")
        if conc:
            conc_color = "red" if conc == "high" else "orange" if conc == "medium" else "green"
            row2_parts.append(f'Concentration: {_badge(conc.upper(), conc_color)}')
        if row2_parts:
            parts.append(f'<p style="margin:4px 0 12px">{"&nbsp;&nbsp;·&nbsp;&nbsp;".join(row2_parts)}</p>')

    # --- Performance stats ---
    perf = data.get("performance", {})
    if perf:
        parts.append("<h4>Performance</h4>")
        parts.append('<div class="stats-grid">')
        for label, key in [("1W Return", "1w_return_pct"), ("1M Return", "1mo_return_pct"),
                            ("SPY 1W", "spy_1w_pct"), ("SPY 1M", "spy_1mo_pct"),
                            ("Alpha 1W", "alpha_1w"), ("Alpha 1M", "alpha_1mo")]:
            v = perf.get(key)
            if v is not None:
                parts.append(_stat_card(label, _fmt_pct(v), _pct_class(v)))
        parts.append('</div>')
        attrib = perf.get("attribution", [])
        if attrib:
            parts.append("<p><strong>Attribution:</strong></p><ul>")
            for a in attrib:
                parts.append(f'<li><strong>{_he(a.get("symbol",""))}</strong> — {_he(a.get("contribution_note",""))}</li>')
            parts.append("</ul>")

    # --- Technicals table ---
    techs = data.get("technicals", [])
    if techs:
        parts.append("<h4>Technicals</h4>")
        parts.append('<table><thead><tr>'
                     '<th>Symbol</th><th>RSI 14</th><th>50 DMA</th><th>200 DMA</th><th>Note</th>'
                     '</tr></thead><tbody>')
        for t in techs:
            rsi = t.get("rsi_14")
            rsi_class = "neg" if rsi and rsi > 70 else "pos" if rsi and rsi < 30 else "neu"

            def _dma(val: object) -> str:
                if val is True:
                    return '<span class="pos">✓ Above</span>'
                if val is False:
                    return '<span class="neg">✗ Below</span>'
                return '<span class="neu">n/a</span>'

            parts.append(
                f'<tr>'
                f'<td><strong>{_he(t.get("symbol",""))}</strong></td>'
                f'<td class="{rsi_class}">{rsi if rsi is not None else "n/a"}</td>'
                f'<td>{_dma(t.get("above_50dma"))}</td>'
                f'<td>{_dma(t.get("above_200dma"))}</td>'
                f'<td style="font-size:0.9em;color:#444">{_he(t.get("technical_note",""))}</td>'
                f'</tr>'
            )
        parts.append('</tbody></table>')

    # --- Holdings flags ---
    flags = data.get("holdings_flags", [])
    if flags:
        parts.append("<h4>Holdings Flags</h4><ul>")
        for f in flags:
            flag_type = f.get("flag", "")
            f_color = "red" if flag_type == "ALERT" else "orange" if flag_type == "WATCH" else "blue"
            lt = f.get("long_term_relevance", "")
            lt_html = f'<br><span style="font-size:0.87em;color:#555">Long-term: {_he(lt)}</span>' if lt else ""
            parts.append(f'<li>{_badge(flag_type, f_color)}&nbsp;<strong>{_he(f.get("symbol",""))}</strong> — {_he(f.get("reason",""))}{lt_html}</li>')
        parts.append("</ul>")

    # --- Notable observations ---
    obs = data.get("notable_observations", [])
    if obs:
        parts.append('<h4>Notable Observations</h4><ul class="obs-list">')
        for o in obs:
            parts.append(f'<li>{_he(o)}</li>')
        parts.append("</ul>")

    return "\n".join(parts)


def _render_agent2_html(data: dict) -> str:
    parts: list[str] = []

    # --- Risk score headline ---
    risk_score = data.get("risk_score")
    trend = data.get("trend", "")
    vs_target = data.get("vs_target", "")
    if risk_score is not None:
        score_color = "red" if risk_score >= 4 else "orange" if risk_score == 3 else "green"
        trend_color = "red" if "up" in str(trend).lower() else "green" if "down" in str(trend).lower() else "grey"
        vt_color = "green" if "appropriate" in str(vs_target).lower() else "orange" if "high" in str(vs_target).lower() else "grey"
        parts.append('<div class="stats-grid" style="margin-bottom:16px">')
        parts.append(_stat_card("Risk Score (1–5)", f"{risk_score}/5", score_color if score_color != "orange" else "neu"))
        if trend:
            parts.append(f'<div class="stat-card"><div class="stat-label">Trend</div>'
                         f'<div class="stat-value">{_badge(trend, trend_color)}</div></div>')
        if vs_target:
            parts.append(f'<div class="stat-card"><div class="stat-label">vs Target</div>'
                         f'<div class="stat-value">{_badge(vs_target, vt_color)}</div></div>')
        parts.append('</div>')

    # --- Risk metrics ---
    metrics = data.get("risk_metrics", {})
    if metrics:
        parts.append("<h4>Risk Metrics</h4>")
        parts.append('<div class="stats-grid">')
        labels = [
            ("Portfolio Beta", "portfolio_beta", "neu"),
            ("Beta Coverage", "beta_coverage_pct", "neu"),
            ("Est. Sharpe", "estimated_sharpe", "neu"),
            ("Max Drawdown", "max_drawdown_pct", "neg"),
            ("VaR 95%", "var_95_pct", "neg"),
        ]
        for label, key, default_class in labels:
            v = metrics.get(key)
            if v is not None:
                css = _pct_class(v) if "pct" in key else default_class
                display = _fmt_pct(v) if "pct" in key and key != "beta_coverage_pct" else str(v)
                if key == "beta_coverage_pct":
                    display = f"{v}%"
                parts.append(_stat_card(label, display, css))
        parts.append('</div>')

    # --- Stress scenarios ---
    scenarios = data.get("stress_scenarios", [])
    if scenarios:
        parts.append("<h4>Stress Scenarios</h4>")
        parts.append('<table><thead><tr>'
                     '<th>Scenario</th><th>Impact (USD)</th><th>Impact (%)</th><th>New NAV</th>'
                     '</tr></thead><tbody>')
        for s in scenarios:
            impact_pct = s.get("impact_pct_of_nav", 0)
            parts.append(
                f'<tr>'
                f'<td>{_he(s.get("scenario",""))}</td>'
                f'<td class="neg">{_fmt_usd(s.get("estimated_impact_usd"))}</td>'
                f'<td class="neg">{_fmt_pct(impact_pct)}</td>'
                f'<td>{_fmt_usd(s.get("estimated_new_nav"))}</td>'
                f'</tr>'
            )
        parts.append('</tbody></table>')

    # --- Correlation & liquidity ---
    corr = data.get("correlation_risks", [])
    if corr:
        parts.append("<h4>Correlation Risks</h4><ul>")
        for c in (corr if isinstance(corr, list) else [corr]):
            parts.append(f'<li>{_he(c)}</li>')
        parts.append("</ul>")

    liq = data.get("liquidity_concerns", [])
    if liq:
        parts.append("<h4>Liquidity Concerns</h4><ul>")
        for c in (liq if isinstance(liq, list) else [liq]):
            parts.append(f'<li>{_he(c)}</li>')
        parts.append("</ul>")

    return "\n".join(parts)


def _render_agent3_html(data: dict) -> str:
    parts: list[str] = []

    # --- Sector summary ---
    summary = data.get("sector_summary", "")
    if summary:
        parts.append(f'<p style="background:#f5f8ff;padding:10px 14px;border-radius:6px;'
                     f'border-left:4px solid #0055cc;margin:8px 0 16px">{_he(summary)}</p>')

    # --- Macro environment ---
    macro = data.get("macro_environment", {})
    if macro and isinstance(macro, dict):
        parts.append("<h4>Macro Environment</h4>")
        parts.append('<table><tbody>')
        for k, v in macro.items():
            parts.append(f'<tr><td style="font-weight:600;white-space:nowrap;width:30%">{_he(k)}</td>'
                         f'<td>{_he(v)}</td></tr>')
        parts.append('</tbody></table>')

    # --- Opportunity candidates ---
    opps = data.get("opportunity_candidates", [])
    if opps:
        parts.append("<h4>Opportunity Candidates</h4>")
        parts.append('<table><thead><tr>'
                     '<th>Symbol</th><th>Name</th><th>Conviction</th><th>Horizon</th><th>Thesis</th>'
                     '</tr></thead><tbody>')
        for o in opps:
            conviction = o.get("conviction", 0)
            try:
                conv_int = int(conviction)
            except (TypeError, ValueError):
                conv_int = 0
            fill_pct = min(100, conv_int * 10)
            conv_bar = (f'<span title="{conv_int}/10">{conv_int}/10'
                        f'<span class="conviction-bar">'
                        f'<span class="conviction-fill" style="width:{fill_pct}%"></span>'
                        f'</span></span>')
            parts.append(
                f'<tr>'
                f'<td><strong>{_he(o.get("symbol",""))}</strong></td>'
                f'<td>{_he(o.get("name",""))}</td>'
                f'<td>{conv_bar}</td>'
                f'<td style="font-size:0.88em">{_he(o.get("horizon",""))}</td>'
                f'<td style="font-size:0.88em">{_he(o.get("thesis",""))}</td>'
                f'</tr>'
            )
        parts.append('</tbody></table>')

    # --- Upcoming events ---
    events = data.get("upcoming_events", [])
    if events:
        parts.append("<h4>Upcoming Events</h4><ul>")
        for e in (events if isinstance(events, list) else [events]):
            parts.append(f'<li>{_he(e)}</li>')
        parts.append("</ul>")

    # --- Resolved from prior ---
    resolved = data.get("resolved_from_prior", [])
    if resolved:
        parts.append('<h4>Resolved from Prior Week</h4><ul>')
        for r in (resolved if isinstance(resolved, list) else [resolved]):
            parts.append(f'<li>{_he(r)}</li>')
        parts.append("</ul>")

    return "\n".join(parts)


def _render_agent4_html(data: dict) -> str:
    parts: list[str] = []

    # --- Rebalancing recommendation box ---
    rec = data.get("rebalancing_recommendation", {})
    if rec:
        action = str(rec.get("action", "")).lower()
        action_color = "red" if action in ("urgent_review", "rebalance") else "green" if action == "hold" else "blue"
        confidence = rec.get("confidence")
        conf_str = f'&nbsp;&nbsp;Confidence: <strong>{confidence}/10</strong>' if confidence is not None else ""
        parts.append('<h4>Rebalancing Recommendation</h4>')
        parts.append(f'<div class="rec-box">'
                     f'<div class="rec-action">{_badge(action.upper().replace("_"," "), action_color)}{conf_str}</div>'
                     f'<div class="rec-rationale">{_he(rec.get("rationale",""))}</div>'
                     f'</div>')

    # --- Opportunity assessment table ---
    opp_assess = data.get("opportunity_assessment", [])
    if opp_assess:
        parts.append("<h4>Opportunity Assessment</h4>")
        parts.append('<table><thead><tr>'
                     '<th>Candidate</th><th>Fit Score</th><th>Recommendation</th><th>Rationale</th>'
                     '</tr></thead><tbody>')
        for o in opp_assess:
            rec_val = str(o.get("recommendation", "")).lower()
            rec_color = "green" if "add" in rec_val else "orange" if "monitor" in rec_val else "red" if "skip" in rec_val else "blue"
            fit = o.get("fit_score")
            fit_str = f"{fit}/10" if fit is not None else "n/a"
            parts.append(
                f'<tr>'
                f'<td><strong>{_he(o.get("candidate",""))}</strong></td>'
                f'<td>{fit_str}</td>'
                f'<td>{_badge(rec_val.upper(), rec_color)}</td>'
                f'<td style="font-size:0.9em">{_he(o.get("rationale",""))}</td>'
                f'</tr>'
            )
        parts.append('</tbody></table>')

    # --- Top-3 observations ---
    top3 = data.get("top_3_observations", [])
    if top3:
        parts.append("<h4>Top Observations</h4>")
        parts.append('<ol class="obs-list">')
        for i, obs in enumerate(top3):
            cls = "top" if i == 0 else ""
            parts.append(f'<li class="{cls}">{_he(obs)}</li>')
        parts.append("</ol>")

    # --- Chronicle entry ---
    chron = data.get("chronicle_entry", {})
    if chron and isinstance(chron, dict):
        parts.append("<h4>Chronicle Entry</h4>")
        parts.append('<table><tbody>')
        for k, v in chron.items():
            if k == "week":
                continue
            display_v = ", ".join(v) if isinstance(v, list) else str(v)
            parts.append(f'<tr><td style="font-weight:600;white-space:nowrap;width:28%">'
                         f'{_he(k.replace("_"," ").title())}</td>'
                         f'<td>{_he(display_v)}</td></tr>')
        parts.append('</tbody></table>')

    return "\n".join(parts)


_AGENT_RENDERERS = {
    "agent 1": _render_agent1_html,
    "agent 2": _render_agent2_html,
    "agent 3": _render_agent3_html,
    "agent 4": _render_agent4_html,
}


def _build_agent_reports_html(agent_reports: dict[str, dict]) -> str:
    """Build rich HTML sections for each agent's output, with raw JSON in a nested collapsible."""
    if not agent_reports:
        return ""
    parts = ['<div class="agent-reports">', '<h2>Agent Reports</h2>']
    for label, data in agent_reports.items():
        # Pick structured renderer by matching label prefix (e.g. "Agent 1 —...")
        renderer = None
        for key, fn in _AGENT_RENDERERS.items():
            if label.lower().startswith(key):
                renderer = fn
                break

        structured_html = renderer(data) if renderer and data else ""

        # Raw JSON nested collapsible
        json_str = json.dumps(data, indent=2, default=str)
        json_str = json_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        raw_html = (f'<details class="json-raw"><summary>Raw JSON</summary>'
                    f'<pre><code>{json_str}</code></pre></details>')

        body = (f'<div class="agent-body">{structured_html}{raw_html}</div>'
                if structured_html else
                f'<div class="agent-body">{raw_html}</div>')

        parts.append(f'<details open><summary>{_he(label)}</summary>{body}</details>')
    parts.append("</div>")
    return "\n".join(parts)


def send_report_email(subject: str, body: str,
                      agent_reports: dict[str, dict] | None = None) -> None:
    """
    Send the weekly report via Gmail using SMTP.
    Converts markdown body to HTML. Plain-text fallback included.
    Reads GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT from env/.env.
    agent_reports: optional dict mapping label -> raw dict, appended as
                   collapsible sections at the bottom of the email.
    Raises RuntimeError if credentials are missing or send fails.
    """
    import smtplib
    import markdown as md_lib
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

    html_body = md_lib.markdown(body, extensions=["extra", "nl2br"])
    agent_reports_html = _build_agent_reports_html(agent_reports or {})
    html = _EMAIL_HTML_TEMPLATE.format(
        content=html_body,
        agent_reports_html=agent_reports_html,
        date=datetime.date.today().isoformat(),
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "plain"))   # fallback
    msg.attach(MIMEText(html, "html"))    # preferred

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())
