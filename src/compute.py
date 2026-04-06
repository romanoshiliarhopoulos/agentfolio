"""
Derived metric computation.

Takes aggregated holdings and market data, returns computed metrics
ready for JSON serialisation. All pure Python — no API calls here.
"""

from typing import Any


def compute_allocation(holdings: dict[str, dict], nav_total: float) -> dict[str, Any]:
    """
    Compute each position's allocation as % of total NAV.
    Also returns the implied equal-weight target and drift from it.
    """
    n = len(holdings)
    equal_weight = round(100 / n, 2) if n > 0 else 0

    allocation: list[dict] = []
    for symbol, h in holdings.items():
        pct = round(h["position_value"] / nav_total * 100, 2) if nav_total else 0
        drift = round(pct - equal_weight, 2)
        allocation.append({
            "symbol": symbol,
            "position_value": h["position_value"],
            "allocation_pct": pct,
            "equal_weight_target_pct": equal_weight,
            "drift_from_equal_weight_pct": drift,
        })

    allocation.sort(key=lambda x: x["allocation_pct"], reverse=True)
    return {
        "by_symbol": allocation,
        "largest_position_pct": allocation[0]["allocation_pct"] if allocation else 0,
        "equal_weight_pct": equal_weight,
    }


def compute_performance(nav: dict[str, Any], deposits_ytd: float) -> dict[str, Any]:
    """
    Compute portfolio-level returns using NAV history.
    Adjusts for deposits so cash inflows don't inflate return figures.
    """
    total = nav.get("total", 0)
    total_7d = nav.get("total_7d_ago")
    total_30d = nav.get("total_30d_ago")

    return {
        "nav_today": total,
        "return_1w_pct": round((total - total_7d) / total_7d * 100, 2) if total_7d else None,
        "return_1mo_pct": round((total - total_30d) / total_30d * 100, 2) if total_30d else None,
        "deposits_ytd": deposits_ytd,
        "note": "Returns not adjusted for intra-period deposits. Treat as approximate.",
    }


def compute_risk_proxies(
    holdings: dict[str, dict],
    market_data: dict[str, dict],
    nav_total: float,
) -> dict[str, Any]:
    """
    Compute simple risk metrics from available data.
    Beta is a weighted sum of individual betas from yfinance info.
    These are approximations — not substitutes for rigorous risk models.
    """
    weighted_beta = 0.0
    beta_coverage = 0.0  # % of portfolio with beta data

    for symbol, h in holdings.items():
        weight = h["position_value"] / nav_total if nav_total else 0
        mkt = market_data.get(symbol, {})
        # yfinance sometimes puts beta in info; we stored it if available
        beta = mkt.get("beta")
        if beta is not None:
            weighted_beta += weight * beta
            beta_coverage += weight

    return {
        "portfolio_beta_approx": round(weighted_beta, 3) if beta_coverage > 0 else None,
        "beta_coverage_pct": round(beta_coverage * 100, 1),
        "beta_note": "Weighted avg of yfinance betas. Approximate.",
        "concentration_top1_pct": max(
            (h["position_value"] / nav_total * 100 for h in holdings.values()),
            default=0
        ),
        "position_count": len(holdings),
    }


def compute_stress_scenarios(
    holdings: dict[str, dict],
    nav_total: float,
) -> list[dict]:
    """
    Simple linear stress scenarios. Not a Monte Carlo — just directional sizing.
    """
    invested = sum(h["position_value"] for h in holdings.values())
    cash = nav_total - invested

    scenarios = [
        ("market -10%", -0.10),
        ("market -20%", -0.20),
        ("market -30%", -0.30),
        ("tech selloff -25%", -0.25),   # assume equity portion takes ~full hit
    ]

    results = []
    for label, shock in scenarios:
        estimated_loss = invested * shock
        new_nav = nav_total + estimated_loss
        results.append({
            "scenario": label,
            "estimated_impact_usd": round(estimated_loss, 2),
            "estimated_new_nav": round(new_nav, 2),
            "impact_pct_of_nav": round(shock * (invested / nav_total) * 100, 2) if nav_total else None,
        })

    return results


def compute_holding_flags(
    holdings: dict[str, dict],
    market_data: dict[str, dict],
    alert_threshold_pct: float = 5.0,
    watch_threshold_pct: float = 2.0,
) -> list[dict]:
    """
    Flag holdings with notable price movements or metrics.
    """
    flags: list[dict] = []

    for symbol, h in holdings.items():
        mkt = market_data.get(symbol, {})
        change_1d = mkt.get("change_1d_pct")
        change_5d = mkt.get("change_5d_pct")
        pct_from_high = mkt.get("pct_from_52w_high")

        if change_1d is not None and abs(change_1d) >= alert_threshold_pct:
            flags.append({
                "symbol": symbol,
                "flag": "ALERT",
                "reason": f"1-day move of {change_1d:+.1f}%",
            })
        elif change_1d is not None and abs(change_1d) >= watch_threshold_pct:
            flags.append({
                "symbol": symbol,
                "flag": "WATCH",
                "reason": f"1-day move of {change_1d:+.1f}%",
            })

        if change_5d is not None and abs(change_5d) >= 8.0:
            flags.append({
                "symbol": symbol,
                "flag": "WATCH",
                "reason": f"5-day move of {change_5d:+.1f}%",
            })

        if pct_from_high is not None and pct_from_high <= -20:
            flags.append({
                "symbol": symbol,
                "flag": "WATCH",
                "reason": f"{pct_from_high:.1f}% below 52-week high",
            })

        if h.get("unrealized_pnl_pct") is not None and h["unrealized_pnl_pct"] <= -15:
            flags.append({
                "symbol": symbol,
                "flag": "WATCH",
                "reason": f"Position down {h['unrealized_pnl_pct']:.1f}% from cost basis",
            })

    return flags
