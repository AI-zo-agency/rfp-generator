"""Deterministic HubSpot + QuickBooks hybrid forecast math.

Python owns every money figure. Spec:
docs/superpowers/specs/2026-10-05-hubspot-forecast-design.md
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

INVOICE_LAG_DAYS = 7
WEEKS = 13
STAGE_FALLBACK = {"early": 0.10, "mid": 0.50, "late": 0.80, "won": 1.0, "lost": 0.0}

_MONTH_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MONTH_LABEL_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})$"
)

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
    """Close month for HubSpot layers; stale past closes roll into as_of month.

    Applies to still-open deals and to Closed Won still awaiting invoice (matched
    ids are filtered before this runs). Past-month locking must not erase uninvoiced wins.
    """
    close = _as_date(deal.get("closedate"))
    if not close:
        return None
    as_of_month = date(as_of.year, as_of.month, 1)
    close_month = date(close.year, close.month, 1)
    if close_month < as_of_month and (
        not deal.get("hs_is_closed") or deal.get("hs_is_closed_won")
    ):
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


def load_deals() -> list[dict[str, Any]]:
    """Active (non-archived) deals from the HubSpot mirror."""
    from app.services.supabase_db import _get_client

    db = _get_client()
    rows: list[dict[str, Any]] = []
    page = 1000
    while True:
        chunk = (
            db.table("hs_deals")
            .select("*")
            .eq("archived", False)
            .order("hs_id")
            .range(len(rows), len(rows) + page - 1)
            .execute()
            .data
            or []
        )
        rows.extend(chunk)
        if len(chunk) < page:
            break
    out: list[dict[str, Any]] = []
    for r in rows:
        close = r.get("closedate")
        out.append({
            **r,
            "amount": _money(r.get("amount")),
            "stage_probability": (
                None if r.get("stage_probability") is None else _money(r.get("stage_probability"))
            ),
            "closedate": _as_date(close),
            "hs_is_closed": bool(r.get("hs_is_closed")),
            "hs_is_closed_won": bool(r.get("hs_is_closed_won")),
            "hs_is_closed_lost": bool(r.get("hs_is_closed_lost")),
        })
    return out


def load_stages() -> dict[tuple[str, str], dict[str, Any]]:
    from app.services.supabase_db import _get_client

    db = _get_client()
    rows = db.table("hs_deal_stages").select("*").execute().data or []
    return {
        (str(r["pipeline_id"]), str(r["stage_id"])): {
            "probability": r.get("probability"),
            "label": r.get("stage_label"),
            "stage_label": r.get("stage_label"),
            "is_closed": bool(r.get("is_closed")),
        }
        for r in rows
    }


def load_companies() -> dict[int, dict[str, Any]]:
    from app.services.supabase_db import _get_client

    db = _get_client()
    rows = db.table("hs_companies").select("hs_id,name,domain").execute().data or []
    return {int(r["hs_id"]): r for r in rows if r.get("hs_id") is not None}


def qb_booked_by_month(overview: dict[str, Any], year: int) -> dict[str, float]:
    """TOTAL INCOME by YYYY-MM from overview monthly_trend."""
    out: dict[str, float] = {}
    months = ((overview.get("monthly_trend") or {}).get("months") or [])
    for row in months:
        label = str(row.get("month") or "").strip()
        m = _MONTH_LABEL_RE.match(label)
        if m:
            y, mo = int(m.group(2)), _MONTH_NUM[m.group(1)]
            if y == year:
                out[f"{y:04d}-{mo:02d}"] = _money(row.get("amount"))
        elif label.startswith(f"{year}-"):
            out[label[:7]] = _money(row.get("amount"))
    return out


def build_hubspot_forecast(
    realm_id: str,
    overview: dict[str, Any],
    *,
    year: int,
    as_of: date,
) -> dict[str, Any] | None:
    """Hybrid year/monthly/cash from HubSpot deals + QB. None if no deals."""
    from app.financial import qb_repository as repo
    from app.financial.hs_forecast_match import match_won_deals
    from app.financial.qb_forecast_llm import (
        collection_curve,
        monthly_outflow,
        open_invoices,
    )

    try:
        deals = load_deals()
    except Exception as exc:  # noqa: BLE001 — mirror may lack table yet
        logger.warning("operation=hs_forecast_load_deals status=failed reason=%s", str(exc)[:200])
        return None
    if not deals:
        logger.info("operation=hs_forecast realm_id=%s status=empty reason=no_deals", realm_id)
        return None

    try:
        stages = load_stages()
        companies = load_companies()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation=hs_forecast_load_meta status=failed reason=%s", str(exc)[:200])
        stages, companies = {}, {}

    invoices = repo.list_invoices(realm_id)
    match = match_won_deals(deals, companies=companies, invoices=invoices, as_of=as_of)
    qb_booked = qb_booked_by_month(overview, year)
    months = monthly_points(
        qb_booked=qb_booked,
        deals=deals,
        stages=stages,
        as_of=as_of,
        year=year,
        matched_won_ids=match.matched_ids,
    )
    y_point = year_point(months)
    remaining_months = 12 - as_of.month + 1

    curve = collection_curve(realm_id)
    outflow_meta = monthly_outflow(realm_id)
    by_m = list((outflow_meta.get("by_month") or {}).values())
    monthly_burn = (sorted(by_m)[len(by_m) // 2] if by_m else 0.0)
    weekly_out = monthly_burn / 4.3

    liquidity = overview.get("liquidity") or {}
    weeks = cash_weeks(
        as_of=as_of,
        cash_on_hand=_money(liquidity.get("cash")),
        open_ar=open_invoices(realm_id, as_of=as_of),
        deals=deals,
        stages=stages,
        collection_curve=curve,
        weekly_outflow=weekly_out,
        matched_won_ids=match.matched_ids,
    )
    trough = trough_from_weeks(weeks)
    comp = composition(months)
    logger.info(
        "operation=hs_forecast realm_id=%s year=%s status=ok year_point=%.0f "
        "weighted_open=%.0f won_awaiting=%.0f unmatched_won=%s",
        realm_id, year, y_point, comp["weighted_open"],
        comp["won_awaiting_invoice"], len(match.unmatched),
    )
    return {
        "cash_13w": {
            "weeks": weeks,
            "trough": trough,
            "low": None,
            "high": None,
            "assumptions": "HubSpot weighted pipeline + QB AR/outflow (deterministic)",
            "risks": [
                "Close dates are sales dates, not cash dates",
                "Unmatched Closed Won still counted at full amount",
            ],
        },
        "year": {
            "point": y_point,
            "low": None,
            "high": None,
            "remaining_months": remaining_months,
            "confidence": "medium",
            "reasoning": "QB booked YTD + HubSpot Closed Won awaiting invoice + weighted open",
        },
        "composition": comp,
        "unmatched_won": match.unmatched,
        "months": months,
        "matched_won_ids": sorted(match.matched_ids),
    }
