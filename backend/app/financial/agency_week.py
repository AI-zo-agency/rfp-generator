"""ISO Mon–Fri week boundaries for Agency weekly AI insights (America/Los_Angeles)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")


def today_pt() -> date:
    return datetime.now(TZ).date()


def week_bounds(day: date) -> tuple[date, date]:
    """Monday and Friday of the ISO week containing day."""
    monday = day - timedelta(days=day.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def prior_week_bounds(day: date) -> tuple[date, date]:
    monday, friday = week_bounds(day)
    return monday - timedelta(days=7), friday - timedelta(days=7)


def iso(d: date) -> str:
    return d.isoformat()


def period_label(start: date, end: date) -> str:
    """Human label: Monday 25 August to Friday 29 August."""
    start_fmt = f"{start.strftime('%A')} {start.day} {start.strftime('%B')}"
    if start.month == end.month:
        end_fmt = f"{end.strftime('%A')} {end.day} {end.strftime('%B')}"
    else:
        end_fmt = f"{end.strftime('%A')} {end.day} {end.strftime('%B')}"
    return f"{start_fmt} to {end_fmt}"


def current_week_label(day: date | None = None) -> str:
    day = day or today_pt()
    start, end = week_bounds(day)
    return f"This week: {period_label(start, end)} (in progress)"


def brief_week_for(day: date | None = None) -> tuple[date, date, str]:
    """The completed Mon–Fri week the Monday brief summarizes."""
    day = day or today_pt()
    start, end = prior_week_bounds(day)
    return start, end, period_label(start, end)
