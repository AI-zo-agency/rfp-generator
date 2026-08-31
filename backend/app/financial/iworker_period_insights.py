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
