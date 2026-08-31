from datetime import date

from app.financial.agency_week import (
    brief_week_for,
    current_week_label,
    iso,
    period_label,
    prior_week_bounds,
    week_bounds,
)


def test_week_bounds_monday_to_friday():
    monday, friday = week_bounds(date(2026, 9, 2))  # Wednesday
    assert monday == date(2026, 8, 31)
    assert friday == date(2026, 9, 4)


def test_prior_week_bounds():
    start, end = prior_week_bounds(date(2026, 9, 7))  # Monday
    assert start == date(2026, 8, 31)
    assert end == date(2026, 9, 4)


def test_period_label_same_month():
    label = period_label(date(2026, 8, 31), date(2026, 9, 4))
    assert label == "Monday 31 August to Friday 4 September"


def test_brief_week_for_uses_prior_completed_week():
    start, end, label = brief_week_for(date(2026, 9, 7))
    assert start == date(2026, 8, 31)
    assert end == date(2026, 9, 4)
    assert "31 August" in label
    assert "4 September" in label


def test_current_week_label_in_progress():
    label = current_week_label(date(2026, 9, 7))
    assert label.startswith("This week:")
    assert "in progress" in label


def test_iso_format():
    assert iso(date(2026, 8, 25)) == "2026-08-25"
