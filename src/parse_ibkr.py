"""
IBKR CSV parser.

The IBKR activity statement is a single CSV file with multiple sections,
each preceded by its own header row. There are no blank-line separators —
sections are identified by matching the column headers.

Sections detected:
  nav_history  — daily NAV breakdown (cash, stock, total)
  pnl_summary  — unrealized + realized PnL per symbol
  positions    — open lots (one row per lot, not per symbol)
  trades       — every executed trade
  cash_txns    — dividends, withholding tax, deposits, withdrawals
  fx_rates     — daily FX rates
"""

import csv
import io
from collections import defaultdict
from datetime import datetime, date
from typing import Any


# Each signature is a list of column names that must ALL be present in a header
# row for us to identify that section. Order doesn't matter.
SECTION_SIGNATURES: dict[str, list[str]] = {
    "nav_history": ["ClientAccountID", "CurrencyPrimary", "ReportDate", "Cash", "Stock"],
    "pnl_summary": ["ClientAccountID", "AssetClass", "Symbol", "Description", "TotalUnrealizedPnl"],
    "positions":   ["ClientAccountID", "CurrencyPrimary", "AssetClass", "Symbol", "Quantity", "MarkPrice", "CostBasisPrice"],
    "trades":      ["ClientAccountID", "CurrencyPrimary", "AssetClass", "Symbol", "DateTime", "TransactionType", "TradePrice"],
    "cash_txns":   ["ClientAccountID", "CurrencyPrimary", "FXRateToBase", "AssetClass", "Date/Time", "SettleDate", "Amount", "Type"],
    "fx_rates":    ["Date/Time", "FromCurrency", "ToCurrency", "Rate"],
}


def _match_section(headers: list[str]) -> str | None:
    header_set = set(headers)
    for section, required in SECTION_SIGNATURES.items():
        if all(col in header_set for col in required):
            return section
    return None


def parse(csv_path: str) -> dict[str, list[dict]]:
    """
    Parse an IBKR activity statement CSV.

    Returns a dict keyed by section name, each value a list of row dicts.
    Rows that are sub-totals or summary lines (e.g. 'Total (All Assets)')
    are excluded.
    """
    sections: dict[str, list[dict]] = defaultdict(list)
    current_section: str | None = None
    current_headers: list[str] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for raw_row in reader:
            # Strip surrounding quotes that some IBKR exports leave on every cell
            row = [cell.strip('"').strip() for cell in raw_row]
            if not any(row):
                continue

            # Check if this row is a section header
            matched = _match_section(row)
            if matched:
                current_section = matched
                current_headers = row
                continue

            # Skip summary/total rows
            if any("Total" in cell or "BASE_SUMMARY" in cell or cell == "CurrencyPrimary" for cell in row):
                continue

            # Skip rows that don't match the expected column count for the current section
            if current_section and current_headers and len(row) == len(current_headers):
                row_dict = dict(zip(current_headers, row))
                sections[current_section].append(row_dict)

    return dict(sections)


def aggregate_positions(position_rows: list[dict]) -> dict[str, dict]:
    """
    Aggregate per-lot position rows into per-symbol holdings.

    Returns a dict keyed by symbol with aggregated position data.
    Excludes CASH rows (e.g. EUR balance held as FX).
    """
    lots: dict[str, list[dict]] = defaultdict(list)

    for row in position_rows:
        if row.get("AssetClass") == "CASH":
            continue
        symbol = row["Symbol"]
        lots[symbol].append(row)

    holdings: dict[str, dict] = {}
    for symbol, symbol_lots in lots.items():
        total_qty = sum(float(lot["Quantity"]) for lot in symbol_lots)
        if total_qty == 0:
            continue

        total_cost = sum(float(lot["Quantity"]) * float(lot["CostBasisPrice"]) for lot in symbol_lots)
        weighted_avg_cost = total_cost / total_qty
        mark_price = float(symbol_lots[0]["MarkPrice"])
        position_value = total_qty * mark_price
        total_pnl = sum(float(lot["FifoPnlUnrealized"]) for lot in symbol_lots)

        # Earliest open date across lots
        open_dates = []
        for lot in symbol_lots:
            dt_str = lot.get("OpenDateTime", "")
            if dt_str:
                try:
                    open_dates.append(datetime.strptime(dt_str.split(",")[0], "%Y-%m-%d").date())
                except ValueError:
                    pass
        first_opened = min(open_dates).isoformat() if open_dates else None

        holdings[symbol] = {
            "symbol": symbol,
            "description": symbol_lots[0]["Description"],
            "asset_class": symbol_lots[0]["AssetClass"],
            "currency": symbol_lots[0]["CurrencyPrimary"],
            "quantity": round(total_qty, 6),
            "mark_price": mark_price,
            "position_value": round(position_value, 4),
            "weighted_avg_cost": round(weighted_avg_cost, 6),
            "total_cost_basis": round(total_cost, 4),
            "unrealized_pnl": round(total_pnl, 4),
            "unrealized_pnl_pct": round((total_pnl / total_cost) * 100, 2) if total_cost else 0,
            "first_opened": first_opened,
            "days_held": (date.today() - datetime.strptime(first_opened, "%Y-%m-%d").date()).days if first_opened else None,
            "lot_count": len(symbol_lots),
        }

    return holdings


def extract_nav_history(nav_rows: list[dict]) -> dict[str, Any]:
    """
    Extract today's NAV and lookback values from the NAV history section.
    Filters to USD rows only.
    """
    usd_rows = [r for r in nav_rows if r.get("CurrencyPrimary") == "USD"]
    if not usd_rows:
        return {}

    # Sort by date, most recent last
    def parse_date(r: dict) -> date:
        try:
            return datetime.strptime(r["ReportDate"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            return date.min

    usd_rows.sort(key=parse_date)

    def nav_at(rows: list[dict], days_ago: int) -> float | None:
        target = date.today()
        for row in reversed(rows):
            d = parse_date(row)
            if (target - d).days >= days_ago:
                total = row.get("Total", "0")
                return float(total) if total else None
        return None

    latest = usd_rows[-1]
    return {
        "date": latest["ReportDate"],
        "cash": float(latest.get("Cash", 0) or 0),
        "stock": float(latest.get("Stock", 0) or 0),
        "total": float(latest.get("Total", 0) or 0),
        "total_7d_ago": nav_at(usd_rows, 7),
        "total_30d_ago": nav_at(usd_rows, 30),
    }


def extract_dividends_ytd(cash_rows: list[dict]) -> dict[str, Any]:
    """
    Sum dividend income and withholding tax paid YTD from cash transactions.
    """
    current_year = str(date.today().year)
    dividends = 0.0
    withholding = 0.0
    dividend_events: list[dict] = []

    for row in cash_rows:
        dt_str = row.get("Date/Time", "")
        if not dt_str.startswith(current_year):
            continue
        txn_type = row.get("Type", "")
        amount = float(row.get("Amount", 0) or 0)

        if txn_type == "Dividends":
            dividends += amount
            dividend_events.append({
                "date": dt_str.split(",")[0],
                "symbol": row.get("Symbol", ""),
                "amount": amount,
                "description": row.get("Description", ""),
            })
        elif txn_type == "Withholding Tax":
            withholding += amount

    return {
        "dividends_ytd": round(dividends, 4),
        "withholding_ytd": round(withholding, 4),
        "net_dividends_ytd": round(dividends + withholding, 4),  # withholding is negative
        "dividend_events": dividend_events,
    }


def extract_deposits_ytd(cash_rows: list[dict]) -> float:
    """Sum all deposits in the current year."""
    current_year = str(date.today().year)
    total = 0.0
    for row in cash_rows:
        if row.get("Type") == "Deposits/Withdrawals" and row.get("Date/Time", "").startswith(current_year):
            total += float(row.get("Amount", 0) or 0)
    return round(total, 2)
