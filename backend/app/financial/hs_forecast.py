"""Deterministic HubSpot + QuickBooks hybrid forecast math.

Python owns every money figure. Spec:
docs/superpowers/specs/2026-10-05-hubspot-forecast-design.md
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

INVOICE_LAG_DAYS = 7
WEEKS = 13
STAGE_FALLBACK = {"early": 0.10, "mid": 0.50, "late": 0.80, "won": 1.0, "lost": 0.0}

_EARLY = ("contact", "discussion", "meeting", "opportunity", "identified")
_MID = ("summary", "proposal", "strategy", "submitted", "top")
_LATE = ("finaliz", "interview", "presentation", "negotiat", "terms")
_WON = ("won",)
_LOST = ("lost",)


def _money(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _fallback_bucket(label: str | None) -> float:
    text = (label or "").lower()
    if any(k in text for k in _LOST):
        return STAGE_FALLBACK["lost"]
    if any(k in text for k in _WON):
        return STAGE_FALLBACK["won"]
    if any(k in text for k in _LATE):
        return STAGE_FALLBACK["late"]
    if any(k in text for k in _MID):
        return STAGE_FALLBACK["mid"]
    if any(k in text for k in _EARLY):
        return STAGE_FALLBACK["early"]
    return STAGE_FALLBACK["mid"]


def _stage_prob(deal: dict[str, Any], stages: dict[tuple[str, str], dict]) -> float:
    if deal.get("hs_is_closed_lost"):
        return 0.0
    if deal.get("hs_is_closed_won"):
        return 1.0
    raw = deal.get("stage_probability")
    if raw is not None and raw != "":
        return max(0.0, min(1.0, _money(raw)))
    key = (str(deal.get("pipeline") or ""), str(deal.get("dealstage") or ""))
    meta = stages.get(key) or {}
    if meta.get("probability") is not None:
        return max(0.0, min(1.0, _money(meta["probability"])))
    label = meta.get("label") or meta.get("stage_label") or ""
    return _fallback_bucket(label)


def weighted_contribution(deal: dict[str, Any], stages: dict[tuple[str, str], dict]) -> float:
    if deal.get("hs_is_closed_lost"):
        return 0.0
    amount = _money(deal.get("amount"))
    if amount <= 0:
        return 0.0
    return round(amount * _stage_prob(deal, stages), 2)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _attribution_month(deal: dict[str, Any], *, as_of: date) -> date | None:
    """Close month for HubSpot layers; stale open past closes roll into as_of month."""
    close = _as_date(deal.get("closedate"))
    if not close:
        return None
    as_of_month = date(as_of.year, as_of.month, 1)
    close_month = date(close.year, close.month, 1)
    if close_month < as_of_month and not deal.get("hs_is_closed"):
        return as_of_month
    return close_month


def monthly_points(
    *,
    qb_booked: dict[str, float],
    deals: list[dict[str, Any]],
    stages: dict[tuple[str, str], dict],
    as_of: date,
    year: int,
    matched_won_ids: set[int] | None = None,
) -> dict[str, dict[str, float]]:
    """Per-month hybrid points for `year`. Past months = QB only."""
    matched = matched_won_ids or set()
    as_of_month = date(as_of.year, as_of.month, 1)
    months: dict[str, dict[str, float]] = {}
    for m in range(1, 13):
        key = f"{year:04d}-{m:02d}"
        months[key] = {
            "qb_booked": round(_money(qb_booked.get(key)), 2),
            "won_awaiting_invoice": 0.0,
            "weighted_open": 0.0,
            "point": 0.0,
        }

    for deal in deals:
        if deal.get("archived"):
            continue
        hs_id = deal.get("hs_id")
        if deal.get("hs_is_closed_won") and hs_id in matched:
            continue
        attr = _attribution_month(deal, as_of=as_of)
        if not attr or attr.year != year:
            continue
        key = _month_key(attr)
        bucket = months.get(key)
        if not bucket:
            continue
        month_start = date(attr.year, attr.month, 1)
        if month_start < as_of_month:
            # Past closed months: QB only — skip HubSpot layers.
            continue
        weight = weighted_contribution(deal, stages)
        if weight <= 0:
            continue
        if deal.get("hs_is_closed_won"):
            bucket["won_awaiting_invoice"] = round(bucket["won_awaiting_invoice"] + weight, 2)
        else:
            bucket["weighted_open"] = round(bucket["weighted_open"] + weight, 2)

    for bucket in months.values():
        bucket["point"] = round(
            bucket["qb_booked"] + bucket["won_awaiting_invoice"] + bucket["weighted_open"],
            2,
        )
    return months


def year_point(months: dict[str, dict[str, float]]) -> float:
    return round(sum(m["point"] for m in months.values()), 2)


def composition(months: dict[str, dict[str, float]]) -> dict[str, float]:
    return {
        "qb_booked": round(sum(m["qb_booked"] for m in months.values()), 2),
        "won_awaiting_invoice": round(sum(m["won_awaiting_invoice"] for m in months.values()), 2),
        "weighted_open": round(sum(m["weighted_open"] for m in months.values()), 2),
    }


def _week_ending(as_of: date, week: int) -> date:
    # Week 1 ends on the Sunday on or after as_of (or as_of itself if Sunday).
    # Keep simple: week N ending = as_of + 7*N days (plan tests care about ordering).
    return as_of + timedelta(days=7 * week)


def _week_index(as_of: date, target: date) -> int | None:
    if target < as_of:
        return None
    delta = (target - as_of).days
    week = delta // 7 + 1
    if week < 1 or week > WEEKS:
        return None
    return week


def _median_days(curve: dict[str, Any] | None) -> int:
    if not curve:
        return 14
    raw = curve.get("median_days")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 14


def cash_weeks(
    *,
    as_of: date,
    cash_on_hand: float,
    open_ar: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    stages: dict[tuple[str, str], dict],
    collection_curve: dict[str, Any] | None,
    weekly_outflow: float,
    matched_won_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Build 13 cash weeks. HubSpot drives from_new_billing only (weighted).

    ponytail: AR timing uses due-or-raised+median into one week, not a full
    collection-curve split. Upgrade path: distribute balance by cumulative_pct.
    """
    matched = matched_won_ids or set()
    median = _median_days(collection_curve)
    from_ar = [0.0] * WEEKS
    from_new = [0.0] * WEEKS

    for inv in open_ar:
        balance = _money(inv.get("balance"))
        if balance <= 0:
            continue
        raised = _as_date(inv.get("raised"))
        due = _as_date(inv.get("due"))
        base = due or (raised + timedelta(days=median) if raised else None)
        if not base:
            continue
        # If already overdue relative to as_of, put in week 1.
        cash_day = max(base, as_of)
        idx = _week_index(as_of, cash_day)
        if idx is None:
            continue
        from_ar[idx - 1] += balance

    for deal in deals:
        if deal.get("archived") or deal.get("hs_is_closed_lost"):
            continue
        hs_id = deal.get("hs_id")
        if deal.get("hs_is_closed_won") and hs_id in matched:
            continue
        weight = weighted_contribution(deal, stages)
        if weight <= 0:
            continue
        close = _as_date(deal.get("closedate")) or as_of
        cash_day = close + timedelta(days=INVOICE_LAG_DAYS + median)
        idx = _week_index(as_of, cash_day)
        if idx is None:
            continue
        from_new[idx - 1] += weight

    weeks: list[dict[str, Any]] = []
    closing = _money(cash_on_hand)
    outflow = max(0.0, _money(weekly_outflow))
    for i in range(WEEKS):
        ar = round(from_ar[i], 2)
        nb = round(from_new[i], 2)
        closing = round(closing + ar + nb - outflow, 2)
        weeks.append({
            "week": i + 1,
            "ending": _week_ending(as_of, i + 1).isoformat(),
            "from_open_invoices": ar,
            "from_new_billing": nb,
            "outflow": round(outflow, 2),
            "closing_balance": closing,
        })
    return weeks


def trough_from_weeks(weeks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not weeks:
        return None
    best = min(weeks, key=lambda w: w["closing_balance"])
    return {"amount": best["closing_balance"], "week": best["week"]}
