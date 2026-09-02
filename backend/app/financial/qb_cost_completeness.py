"""How much of a month's cost has actually landed yet.

Income is booked when the invoice is raised; cost is booked when somebody enters
the bill. Those are not the same day. Fitted on this ledger, a month's cost is
only ~62% present two days after month end, ~69% at two weeks, ~85% at two
months, and still ~93% at four.

So every margin figure for a recent month is too high, and it is worst on the
first of the month when the owner is most likely to be looking. August 2026 read
68.9% gross margin on $29,116 of cost against a $65-70k monthly norm; it was not
a record month, it was an unfinished one. Across Jan-Aug 2026 the effect is 4.6
points of gross margin — 52.9% reported against 48.3% once the $51k of cost still
in transit is counted.

This does **not** rewrite the P&L. QuickBooks stays the source of truth and a
grossed-up cost is a number no ledger contains. It follows what `qb_trend`
already does with the newest booked month and what `_attribution` does with
unallocated cost: name the gap and let the reader discount, rather than
manufacture a precise-looking figure. `settled_through` is the useful output —
the last month whose margin can be quoted without a caveat.

`expected_cost` is offered for sizing the gap, clearly separate from
`booked_cost`, and should never be presented as an actual.

One known bias, stated rather than discovered later: `qbo_updated_at` is the last
update, not the creation. A bill entered on day 1 and edited on day 50 measures
as day 50, so the curve reads later than reality and completeness is understated.
The error is one-directional — this reports *more* missing cost than there is,
never less.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.financial import qb_repository as repo
from app.financial.quickbooks import _MONTH_LABELS

logger = logging.getLogger(__name__)

# Ages at which the curve is sampled. Dense early, where cost is still arriving
# and the correction matters; sparse later, where it has flattened.
_CHECKPOINTS = (0, 2, 7, 14, 21, 30, 45, 60, 90, 120)

# A month is only usable as a curve sample once its own cost has stopped moving.
_SETTLED_AGE_DAYS = 150

# Below this, a month's margin is not quotable.
_SETTLED_PCT = 95.0

# Months whose total cost is too small to be evidence of anything.
_MIN_MONTH_COST = 5_000.0

# Fewer sample months than this and the curve is noise; the panel degrades
# rather than shipping a correction built on two observations.
_MIN_SAMPLES = 6


def _month_end(year: int, month_index: int) -> date:
    """month_index is 0-based, matching _MONTH_LABELS."""
    if month_index == 11:
        return date(year, 12, 31)
    return date(year, month_index + 2, 1) - timedelta(days=1)


def _cost_rows(realm_id: str, since: str) -> list[tuple[date, date, float]]:
    """(txn_date, first_seen, amount) for every cost document we mirror.

    Bills and purchases both hit cost of services and both arrive late, so both
    belong in the curve. Rows missing either date are dropped rather than
    guessed at.
    """
    rows: list[tuple[date, date, float]] = []
    for lister in (repo.list_bills, repo.list_purchases):
        for row in lister(realm_id, txn_date__gte=since):
            if row.get("is_deleted"):
                continue
            txn, seen = row.get("txn_date"), row.get("qbo_updated_at")
            if not txn or not seen:
                continue
            try:
                txn_d = date.fromisoformat(str(txn)[:10])
                seen_d = date.fromisoformat(str(seen)[:10])
            except ValueError:
                continue
            if seen_d < txn_d:
                # Backdated entry: it was already there. Age zero, not negative.
                seen_d = txn_d
            rows.append((txn_d, seen_d, float(row.get("total_amt") or 0)))
    return rows


def _curve(rows: list[tuple[date, date, float]], as_of: date) -> list[dict[str, Any]] | None:
    """Median share of a month's final cost present N days after month end.

    Median rather than mean: one month with a single large late bill would drag
    an average and quietly overstate how much cost is still to come for every
    other month.
    """
    by_month: dict[tuple[int, int], list[tuple[date, float]]] = {}
    for txn_d, seen_d, amount in rows:
        by_month.setdefault((txn_d.year, txn_d.month), []).append((seen_d, amount))

    samples: dict[int, list[float]] = {age: [] for age in _CHECKPOINTS}
    used = 0
    for (year, month), entries in by_month.items():
        end = _month_end(year, month - 1)
        if (as_of - end).days < _SETTLED_AGE_DAYS:
            continue
        final = sum(amount for _, amount in entries)
        if final < _MIN_MONTH_COST:
            continue
        used += 1
        for age in _CHECKPOINTS:
            cutoff = end + timedelta(days=age)
            booked = sum(amount for seen, amount in entries if seen <= cutoff)
            samples[age].append(booked / final)

    if used < _MIN_SAMPLES:
        logger.warning(
            "operation=cost_completeness step=curve status=insufficient_samples months=%s",
            used,
        )
        return None

    curve: list[dict[str, Any]] = []
    running = 0.0
    for age in _CHECKPOINTS:
        # Monotonic by construction per month, but the median across months can
        # dip; a completeness curve that goes backwards would produce negative
        # expected cost downstream.
        running = max(running, median(samples[age]) * 100)
        curve.append({"days": age, "pct": round(min(running, 100.0), 1)})
    return curve


def _completeness_at(curve: list[dict[str, Any]], age_days: int) -> float:
    if age_days >= _CHECKPOINTS[-1]:
        return 100.0
    previous = curve[0]
    for point in curve:
        if point["days"] >= age_days:
            span = point["days"] - previous["days"]
            if span <= 0:
                return point["pct"]
            step = (point["pct"] - previous["pct"]) / span
            return round(previous["pct"] + step * (age_days - previous["days"]), 1)
        previous = point
    return 100.0


def cost_completeness(
    realm_id: str,
    year: int,
    *,
    as_of: date,
    monthly_trend: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Which of this year's months have their costs in, and which do not.

    Returns None when the curve cannot be built or the trend carries no cost
    rows — the same degradation rule as every other panel, so the tab loses one
    section instead of the page.
    """
    months = (monthly_trend or {}).get("months") or []
    booked = [m for m in months if m.get("cost_of_services") is not None and m.get("amount")]
    if not booked:
        return None

    curve = _curve(_cost_rows(realm_id, f"{year - 2}-01-01"), as_of)
    if curve is None:
        return None

    rows: list[dict[str, Any]] = []
    settled_through: str | None = None
    for month in booked:
        label = str(month.get("month") or "")
        short = label.split()[0] if label else ""
        if short not in _MONTH_LABELS:
            continue
        end = _month_end(year, _MONTH_LABELS.index(short))
        age = (as_of - end).days
        if age < 0:
            continue
        pct = _completeness_at(curve, age)
        cost = float(month["cost_of_services"])
        is_settled = pct >= _SETTLED_PCT
        if is_settled:
            settled_through = label
        rows.append({
            "month": label,
            "age_days": age,
            "completeness_pct": pct,
            "booked_cost": round(cost, 2),
            # What the month's cost is heading for, for sizing the gap only.
            "expected_cost": round(cost / (pct / 100), 2) if pct > 0 else None,
            "settled": is_settled,
        })

    if not rows:
        return None

    unsettled = [r for r in rows if not r["settled"]]

    def _margin(selected: list[dict[str, Any]], key: str) -> float | None:
        chosen = {r["month"] for r in selected}
        income = sum(float(m["amount"]) for m in booked if m["month"] in chosen)
        cost = sum(r[key] or 0 for r in selected)
        return round((income - cost) / income * 100, 1) if income else None

    # There is deliberately no margin-over-settled-months-only figure here.
    # It reads like a corrected version of the reported one and is not: on this
    # ledger it came out *higher* (56.1% against 52.9%) simply because Jan-Apr
    # were better months than May-Aug. Two margins over two periods, one of them
    # labelled as the trustworthy one, is how a reader ends up quoting a real
    # number about the wrong span. Only like-for-like figures ship.
    result = {
        "as_of": as_of.isoformat(),
        "curve": curve,
        # The last month whose own margin can be quoted without a caveat. Use it
        # to caveat a month, not to build a year-to-date figure.
        "settled_through": settled_through,
        "unsettled_months": [r["month"] for r in unsettled],
        "missing_cost": round(
            sum((r["expected_cost"] or r["booked_cost"]) - r["booked_cost"] for r in unsettled),
            2,
        ),
        # Same span, two treatments of the same cost. This pair is comparable.
        "reported_gross_margin_pct": _margin(rows, "booked_cost"),
        "adjusted_gross_margin_pct": _margin(rows, "expected_cost"),
        "months": rows,
    }
    reported = result["reported_gross_margin_pct"]
    adjusted = result["adjusted_gross_margin_pct"]
    result["overstated_points"] = (
        round(reported - adjusted, 1) if reported is not None and adjusted is not None else None
    )
    logger.info(
        "operation=cost_completeness realm_id=%s year=%s as_of=%s settled_through=%s "
        "unsettled=%s missing_cost=%s reported_gm=%s adjusted_gm=%s overstated_points=%s",
        realm_id,
        year,
        as_of.isoformat(),
        settled_through,
        len(unsettled),
        result["missing_cost"],
        result["reported_gross_margin_pct"],
        result["adjusted_gross_margin_pct"],
        result["overstated_points"],
    )
    return result
