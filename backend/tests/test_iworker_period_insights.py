from datetime import date

from app.financial.iworker_period_insights import (
    month_bounds,
    month_label,
    parse_entry_date,
    week_bounds,
    week_label,
)


def test_parse_entry_date_sheet_format():
    assert parse_entry_date("May 15, 2026") == date(2026, 5, 15)
    assert parse_entry_date("Dec 30, 2025") == date(2025, 12, 30)
    assert parse_entry_date("") is None
    assert parse_entry_date("not-a-date") is None
    assert parse_entry_date("May 15, 2026 ") == date(2026, 5, 15)


def test_week_bounds_monday_through_sunday():
    # Thursday May 14, 2026 → Mon May 11 … Sun May 17
    assert week_bounds(date(2026, 5, 14)) == (date(2026, 5, 11), date(2026, 5, 17))
    assert week_bounds(date(2026, 5, 11)) == (date(2026, 5, 11), date(2026, 5, 17))
    assert week_bounds(date(2026, 5, 17)) == (date(2026, 5, 11), date(2026, 5, 17))


def test_week_label_emphasizes_mon_fri():
    assert week_label(date(2026, 5, 11)) == "May 11–15, 2026"


def test_month_bounds_and_label():
    assert month_bounds(date(2026, 5, 27)) == (date(2026, 5, 1), date(2026, 5, 31))
    assert month_label(date(2026, 5, 1)) == "May 2026"
