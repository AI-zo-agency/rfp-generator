"""Calendar week/month insights for iWorker timesheets (America/Los_Angeles)."""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TZ_NAME = "America/Los_Angeles"
DEFAULT_WEEKLY_EXPECTED_HOURS = 20.0
AGGREGATE_CONTRACTOR = "*"
UNDERLOGGED_ELAPSED_FRAC = 0.60
UNDERLOGGED_WEEKDAY_MIN = 2  # Wednesday
UNDERLOGGED_HOURS_FRAC = 0.50
OVERCAPACITY_UTIL = 0.85
SPEND_SPIKE_PCT = 25.0
SCOPE_RISK_MIN_USD = 1.0
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y")


def tzinfo(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or getattr(settings, "iworker_tz", None) or DEFAULT_TZ_NAME)


def today_in_tz(now: datetime | None = None, tz_name: str | None = None) -> date:
    zone = tzinfo(tz_name)
    current = now.astimezone(zone) if now else datetime.now(zone)
    return current.date()


def parse_entry_date(date_str: str) -> date | None:
    text = (date_str or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def month_bounds(day: date) -> tuple[date, date]:
    last = calendar.monthrange(day.year, day.month)[1]
    return date(day.year, day.month, 1), date(day.year, day.month, last)


def previous_week_bounds(day: date) -> tuple[date, date]:
    monday, _ = week_bounds(day)
    prior = monday - timedelta(days=1)
    return week_bounds(prior)


def previous_month_bounds(day: date) -> tuple[date, date]:
    start, _ = month_bounds(day)
    return month_bounds(start - timedelta(days=1))


def week_label(monday: date) -> str:
    friday = monday + timedelta(days=4)
    if monday.year == friday.year and monday.month == friday.month:
        return f"{monday.strftime('%B')} {monday.day}–{friday.day}, {monday.year}"
    return f"{monday.strftime('%b')} {monday.day}–{friday.strftime('%b')} {friday.day}, {friday.year}"


def month_label(start: date) -> str:
    return start.strftime("%B %Y")


def _kpi_eligible(entry: dict[str, Any], parsed: date | None) -> bool:
    if parsed is None:
        return False
    return float(entry.get("hours") or 0) > 0


def build_period_metrics(
    entries: list[dict[str, Any]],
    start: date,
    end: date,
    contractor_filter: str | None = None,
) -> tuple[dict[str, Any], int]:
    hours = 0.0
    spend = 0.0
    scope = 0.0
    count = 0
    contractors: set[str] = set()
    unparsed = 0
    want = (contractor_filter or "").strip().lower()
    if want in ("", "all"):
        want = ""

    for entry in entries:
        parsed = parse_entry_date(str(entry.get("date") or ""))
        if parsed is None:
            unparsed += 1
            continue
        name = str(entry.get("contractor") or "").strip()
        if want and name.lower() != want:
            continue
        if parsed < start or parsed > end:
            continue
        if not _kpi_eligible(entry, parsed):
            continue
        hrs = float(entry.get("hours") or 0)
        amt = float(entry.get("amount") or 0)
        hours += hrs
        spend += amt
        count += 1
        if name:
            contractors.add(name)
        if (entry.get("ai_classification") or {}).get("is_over_scope"):
            scope += amt

    return (
        {
            "hours": round(hours, 2),
            "spend_usd": round(spend, 2),
            "scope_risk_usd": round(scope, 2),
            "entries_count": count,
            "active_contractors": len(contractors),
        },
        unparsed,
    )


def pct_delta(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100.0, 1)


def _period_window(
    granularity: str, day: date
) -> tuple[date, date, date, date, str, str]:
    if granularity == "month":
        start, end = month_bounds(day)
        prev_start, prev_end = previous_month_bounds(day)
        return start, end, prev_start, prev_end, month_label(start), month_label(prev_start)
    start, end = week_bounds(day)
    prev_start, prev_end = previous_week_bounds(day)
    return start, end, prev_start, prev_end, week_label(start), week_label(prev_start)


def _resolve_selected_day(granularity: str, period_start: str | None, today: date) -> date:
    if not period_start:
        return today
    try:
        return date.fromisoformat(period_start)
    except ValueError:
        logger.warning("operation=iworker_period status=invalid_period_start value=%s", period_start)
        return today


def _available_periods(entries: list[dict[str, Any]], granularity: str, current_start: date) -> list[dict[str, str]]:
    starts: set[date] = {current_start}
    for entry in entries:
        parsed = parse_entry_date(str(entry.get("date") or ""))
        if parsed is None or not _kpi_eligible(entry, parsed):
            continue
        start, _ = week_bounds(parsed) if granularity != "month" else month_bounds(parsed)
        starts.add(start)
    rows = []
    for start in sorted(starts, reverse=True):
        end = week_bounds(start)[1] if granularity != "month" else month_bounds(start)[1]
        label = week_label(start) if granularity != "month" else month_label(start)
        rows.append({"start": start.isoformat(), "end": end.isoformat(), "label": label})
    return rows


def _contractor_names(entries: list[dict[str, Any]]) -> list[str]:
    names = []
    seen = set()
    for entry in entries:
        name = str(entry.get("contractor") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _contractor_rate(entries: list[dict[str, Any]], name: str) -> float:
    want = name.lower()
    for entry in entries:
        if str(entry.get("contractor") or "").strip().lower() != want:
            continue
        try:
            return float(entry.get("rate") or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def load_expected_hours_map() -> dict[str, float]:
    raw = (getattr(settings, "iworker_expected_hours_json", None) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("operation=iworker_expected_hours status=invalid_json")
        return {}
    out: dict[str, float] = {}
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            try:
                out[str(key).strip()] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def expected_hours_for_period(
    granularity: str,
    start: date,
    end: date,
    today: date,
    default_weekly: float,
    contractor: str | None,
    expected_map: dict[str, float],
) -> float:
    weekly = default_weekly
    if contractor:
        for key, value in expected_map.items():
            if key.lower() == contractor.lower():
                weekly = value
                break
    in_progress = start <= today <= end
    if granularity == "month":
        days_in_month = (end - start).days + 1
        elapsed = ((min(today, end) - start).days + 1) if in_progress else days_in_month
        return weekly * (elapsed / 7.0)
    elapsed = ((min(today, end) - start).days + 1) if in_progress else 7
    return weekly * (elapsed / 7.0)


def build_period_signals(insights: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    selected = insights["selected"]
    spend_pct = (insights.get("delta") or {}).get("spend_pct")
    if spend_pct is not None and spend_pct >= SPEND_SPIKE_PCT:
        signals.append(
            {
                "id": "iworker:spend_spike",
                "severity": "cost",
                "headline": f"Contractor spend is up {spend_pct}% vs last period",
                "detail": "Review hours mix before this week's invoice.",
                "contractor": None,
            }
        )
    current_scope = insights["current"]["scope_risk_usd"]
    prev_scope = insights["previous_metrics"]["scope_risk_usd"]
    scope_pct = (insights.get("delta") or {}).get("scope_risk_pct")
    if current_scope >= SCOPE_RISK_MIN_USD and (
        prev_scope == 0 or (scope_pct is not None and scope_pct > 0) or current_scope > prev_scope
    ):
        signals.append(
            {
                "id": "iworker:scope_risk:all",
                "severity": "scope",
                "headline": f"${current_scope:.2f} over-scope risk this period",
                "detail": "Intervene on R3+ work before invoicing.",
                "contractor": None,
            }
        )
    today = today_in_tz(now, insights.get("timezone"))
    start = date.fromisoformat(selected["start"])
    end = date.fromisoformat(selected["end"])
    elapsed_frac = ((min(today, end) - start).days + 1) / ((end - start).days + 1)
    late_enough = (
        (not selected.get("is_current"))
        or today.weekday() >= UNDERLOGGED_WEEKDAY_MIN
        or elapsed_frac >= UNDERLOGGED_ELAPSED_FRAC
    )
    for row in insights.get("contractors") or []:
        name = row["name"]
        util = row.get("utilization_pct")
        expected = row.get("expected_hours") or 0
        if util is not None and util >= OVERCAPACITY_UTIL * 100:
            signals.append(
                {
                    "id": f"iworker:overcapacity:{name}",
                    "severity": "capacity",
                    "headline": f"{name} is at {util}% of expected hours",
                    "detail": "Rebalance load before overtime compounds.",
                    "contractor": name,
                }
            )
        elif (
            selected.get("is_current")
            and late_enough
            and expected > 0
            and row["hours"] < expected * UNDERLOGGED_HOURS_FRAC
        ):
            signals.append(
                {
                    "id": f"iworker:underlogged:{name}",
                    "severity": "capacity",
                    "headline": f"{name} is under-logged this week",
                    "detail": f"{row['hours']} hrs vs {expected:.1f} expected so far — chase missing logs or reassign.",
                    "contractor": name,
                }
            )
    logger.info(
        "operation=iworker_signals count=%s ids=%s",
        len(signals),
        ",".join(s["id"] for s in signals),
    )
    return signals


def build_period_insights(
    entries: list[dict[str, Any]],
    *,
    granularity: str = "week",
    period_start: str | None = None,
    contractor: str | None = None,
    now: datetime | None = None,
    expected_hours_by_contractor: dict[str, float] | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    grain = "month" if granularity == "month" else "week"
    tz_name = timezone_name or getattr(settings, "iworker_tz", None) or DEFAULT_TZ_NAME
    today = today_in_tz(now, tz_name)
    selected_day = _resolve_selected_day(grain, period_start, today)
    start, end, prev_start, prev_end, label, prev_label = _period_window(grain, selected_day)
    current_start, _ = _period_window(grain, today)[:2]
    current, unparsed = build_period_metrics(entries, start, end, contractor)
    previous, _ = build_period_metrics(entries, prev_start, prev_end, contractor)
    expected_map = expected_hours_by_contractor or load_expected_hours_map()
    expected = expected_hours_for_period(
        grain, start, end, today, DEFAULT_WEEKLY_EXPECTED_HOURS, contractor, expected_map
    )
    contractors = []
    names = [contractor] if contractor and contractor.lower() != "all" else _contractor_names(entries)
    for name in names:
        cur, _ = build_period_metrics(entries, start, end, name)
        prev, _ = build_period_metrics(entries, prev_start, prev_end, name)
        exp = expected_hours_for_period(grain, start, end, today, DEFAULT_WEEKLY_EXPECTED_HOURS, name, expected_map)
        util = round((cur["hours"] / exp) * 100.0, 1) if exp else None
        contractors.append(
            {
                "name": name,
                "rate": _contractor_rate(entries, name),
                "hours": cur["hours"],
                "spend_usd": cur["spend_usd"],
                "scope_risk_usd": cur["scope_risk_usd"],
                "expected_hours": round(exp, 2),
                "utilization_pct": util,
                "hours_delta_pct": pct_delta(cur["hours"], prev["hours"]),
                "spend_delta_pct": pct_delta(cur["spend_usd"], prev["spend_usd"]),
            }
        )
    payload = {
        "timezone": tz_name,
        "granularity": grain,
        "selected": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": label,
            "is_current": start == current_start,
        },
        "previous": {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "label": prev_label,
        },
        "current": current,
        "previous_metrics": previous,
        "delta": {
            "hours_pct": pct_delta(current["hours"], previous["hours"]),
            "spend_pct": pct_delta(current["spend_usd"], previous["spend_usd"]),
            "scope_risk_pct": pct_delta(current["scope_risk_usd"], previous["scope_risk_usd"]),
        },
        "contractors": contractors,
        "signals": [],
        "available_periods": _available_periods(entries, grain, current_start),
        "unparsed_date_count": unparsed,
        "expected_hours": round(expected, 2),
    }
    payload["signals"] = build_period_signals(payload, now=now)
    logger.info(
        "operation=iworker_period granularity=%s start=%s end=%s contractor=%s hours=%s",
        grain,
        start.isoformat(),
        end.isoformat(),
        contractor or "all",
        current["hours"],
    )
    return payload
