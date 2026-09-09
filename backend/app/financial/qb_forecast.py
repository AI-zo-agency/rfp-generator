"""Forward-looking panels: what is already committed, and what is only expected.

Every figure here was chosen by backtest against this ledger rather than by
reputation. The measured errors, over rolling origins on 32 months of history:

    full year, from 8 months booked      ~10%   ytd / months * 12
    next quarter                        ~19%    median month * 3
    next month                          ~26%    -- not built, see below

There is deliberately no monthly revenue forecast. Eight statistical methods and
ten language models were tested on it; the best was 19.8% and the median month
swings 34% around its own mean. A number that wrong, shown as a number, spends
credibility it cannot earn back.

The quarter figure at ~19% is close to the same objection and is labelled
`low_confidence` so a caller has to opt into showing it.

`billing_gaps` is not a forecast at all and is the most useful thing in the file.
It reads which clients have stopped invoicing on their own established cadence.
EverFast Fiber billed $30,000 within the same week of every month for six months
and then stopped; on 3 September 2026 that was 53 days of silence from 20% of
revenue, and no forecast in this module would have caught it. A client that has
gone quiet is a fact, available today, worth more than any projection of it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from statistics import median
from typing import Any

from app.financial import qb_repository as repo

logger = logging.getLogger(__name__)

# Below this many separate billing events there is no cadence to be late against.
_MIN_EVENTS = 4

# Cadence is read from recent events only. EverFast's 2025 pattern raised three
# invoices across separate days each month, which drags a whole-history median
# down to 14 days and describes a monthly retainer as fortnightly. The last
# twelve gaps track what the client does now.
_CADENCE_WINDOW = 13

# A gap counts as a gap once it exceeds both of these. The multiplier catches
# slow payers on long cycles; the flat addition stops a client who bills every
# 7 days being flagged on day 11.
_GAP_MULTIPLIER = 1.5
_GAP_FLOOR_DAYS = 10

# Past this, silence is not news. A client four cycles gone has churned, and
# leaving it in the alert buries the retainer that stopped last month under a
# list of departures everybody already knows about.
_STALE_CYCLES = 4
_STALE_DAYS = 120

# Clients too small to be worth an alert even when they do go quiet.
_MIN_TRAILING_REVENUE = 5_000.0

_QUARTER_MONTHS = 3


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _income_series(monthly_trend: dict[str, Any] | None) -> list[float]:
    """Booked months only. A trailing zero is an unstarted month, not a bad one."""
    months = (monthly_trend or {}).get("months") or []
    return [float(m["amount"]) for m in months if m.get("amount")]


def billing_gaps(realm_id: str, *, as_of: date) -> dict[str, Any]:
    """Clients that have stopped invoicing relative to their own rhythm.

    Cadence is per client and self-referential: a monthly retainer is late at 45
    days, a quarterly project client is not. Comparing every client to one
    company-wide average would bury exactly the retainer churn this is for.

    Invoices sharing a date collapse into one billing event. EverFast raises
    three lines on the same day most months; counted separately they would imply
    a cadence of zero days and never register as late.
    """
    invoices = repo.list_invoices(realm_id)
    by_client: dict[str, set[date]] = {}
    revenue: dict[str, float] = {}
    year_ago = date(as_of.year - 1, as_of.month, min(as_of.day, 28))

    for invoice in invoices:
        if invoice.get("is_deleted"):
            continue
        txn = _as_date(invoice.get("txn_date"))
        name = invoice.get("customer_name")
        if not txn or not name or txn > as_of:
            continue
        by_client.setdefault(name, set()).add(txn)
        if txn >= year_ago:
            revenue[name] = revenue.get(name, 0.0) + float(invoice.get("total_amt") or 0)

    rows: list[dict[str, Any]] = []
    for name, dates in by_client.items():
        ordered = sorted(dates)
        if len(ordered) < _MIN_EVENTS:
            continue
        trailing = revenue.get(name, 0.0)
        if trailing < _MIN_TRAILING_REVENUE:
            continue
        recent = ordered[-_CADENCE_WINDOW:]
        gaps = [(b - a).days for a, b in zip(recent, recent[1:]) if (b - a).days > 0]
        if not gaps:
            continue
        typical = median(gaps)
        silent = (as_of - ordered[-1]).days
        if silent <= max(typical * _GAP_MULTIPLIER, typical + _GAP_FLOOR_DAYS):
            continue
        if silent > max(typical * _STALE_CYCLES, _STALE_DAYS):
            continue
        rows.append({
            "client": name,
            "last_invoice": ordered[-1].isoformat(),
            "days_silent": silent,
            "typical_gap_days": round(typical, 1),
            # How far past its own rhythm, which is what makes a 53-day gap
            # alarming for one client and unremarkable for another.
            "overdue_ratio": round(silent / typical, 2) if typical else None,
            "trailing_12mo_revenue": round(trailing, 2),
            "billing_events": len(ordered),
        })

    # Revenue at risk, not lateness: a tiny client six months quiet matters less
    # than the largest account three weeks late.
    rows.sort(key=lambda r: -r["trailing_12mo_revenue"])
    total = round(sum(r["trailing_12mo_revenue"] for r in rows), 2)
    logger.info(
        "operation=billing_gaps realm_id=%s as_of=%s flagged=%s revenue_at_risk=%s",
        realm_id,
        as_of.isoformat(),
        len(rows),
        total,
    )
    return {"as_of": as_of.isoformat(), "clients": rows, "revenue_at_risk": total}


def year_projection(monthly_trend: dict[str, Any] | None, *, year: int) -> dict[str, Any] | None:
    """Full year as booked-to-date scaled to twelve months.

    Backtested at ~10% mean error over 18 origins, and it tightens as the year
    closes: under 3% once nine months are booked, because by then the answer is
    mostly arithmetic on months that already happened.

    This is the Python line that runs *beside* the model's forecast, not instead
    of it. Two independently derived numbers disagreeing is the signal; one
    number alone cannot tell a reader it is in trouble.
    """
    booked = _income_series(monthly_trend)
    if not booked:
        return None
    months = len(booked)
    ytd = sum(booked)
    projection = ytd / months * 12
    return {
        "method": "ytd / months_booked * 12",
        "months_booked": months,
        "ytd": round(ytd, 2),
        "point": round(projection, 2),
        # Error shrinks with each month booked; quoting the all-origin average
        # in December would overstate the doubt by several times.
        "expected_error_pct": 10.0 if months < 9 else 3.0,
        "year": year,
    }


def multi_year_income(realm_id: str, year: int, monthly_trend: dict[str, Any] | None) -> list[float]:
    """Booked months across the mirrored years, oldest first.

    The current year alone is never enough — in January it is one month. Prior
    years come from their own cached panels, which are already built and cost a
    key lookup each. A year missing from the cache is skipped rather than fatal.
    """
    series: list[float] = []
    for past in (year - 2, year - 1):
        cached = repo.get_panel_cache(realm_id, past) or {}
        trend = (cached.get("payload") or {}).get("monthly_trend")
        series.extend(_income_series(trend))
    series.extend(_income_series(monthly_trend))
    return series


def quarter_projection(history: list[float]) -> dict[str, Any] | None:
    """Next three months as the median booked month, times three.

    Median rather than mean, and the whole history rather than a trailing window:
    tested over 18 origins the two are within noise (18.8% against 19.2%), but
    the median is not dragged by a single $213k project month the way a mean is.

    18.8% is not a good forecast. It is flagged `low_confidence` for that reason
    — one scored holdout came in at 0.2%, which is luck, not skill, and quoting
    that number anywhere would be dishonest.
    """
    booked = [v for v in history if v]
    if len(booked) < 12:
        return None
    typical = median(booked)
    return {
        "method": "median booked month * 3",
        "point": round(typical * _QUARTER_MONTHS, 2),
        "monthly_basis": round(typical, 2),
        "expected_error_pct": 18.8,
        "low_confidence": True,
        "months_of_history": len(booked),
    }


def forecast(
    realm_id: str,
    year: int,
    *,
    as_of: date,
    monthly_trend: dict[str, Any] | None,
) -> dict[str, Any]:
    """The forward panel. Degrades member by member rather than all or nothing."""
    history = multi_year_income(realm_id, year, monthly_trend)
    panel = {
        "as_of": as_of.isoformat(),
        "billing_gaps": billing_gaps(realm_id, as_of=as_of),
        "year": year_projection(monthly_trend, year=year),
        "quarter": quarter_projection(history),
        # Stated rather than omitted, so nobody adds one back without reading why.
        "month": None,
        "month_omitted_reason": (
            "Best measured monthly error was 19.8% across ten models and eight "
            "statistical methods. Not shippable as a figure."
        ),
    }
    logger.info(
        "operation=forecast realm_id=%s year=%s as_of=%s year_point=%s quarter_point=%s",
        realm_id,
        year,
        as_of.isoformat(),
        (panel["year"] or {}).get("point"),
        (panel["quarter"] or {}).get("point"),
    )
    return panel
