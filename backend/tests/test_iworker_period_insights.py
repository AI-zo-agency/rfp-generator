from datetime import date

from app.financial.iworker_period_insights import (
    build_period_metrics,
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


def _entry(**kwargs):
    row = {
        "date": "May 13, 2026",
        "hours": 4.0,
        "amount": 50.0,
        "contractor": "Murilo",
        "task": "Edits",
        "rate": 12.5,
        "ai_classification": {"is_over_scope": False},
    }
    row.update(kwargs)
    return row


def test_weekend_hours_count_in_monday_week():
    entries = [
        _entry(date="May 15, 2026", hours=5.0, amount=62.5),  # Friday
        _entry(date="May 16, 2026", hours=3.0, amount=37.5),  # Saturday
    ]
    metrics, unparsed = build_period_metrics(entries, date(2026, 5, 11), date(2026, 5, 17))
    assert unparsed == 0
    assert metrics["hours"] == 8.0
    assert metrics["spend_usd"] == 100.0
    assert metrics["entries_count"] == 2


def test_zero_hour_rows_excluded_from_kpis():
    entries = [
        _entry(date="May 13, 2026", hours=0, amount=0, task="Weekend"),
        _entry(date="May 13, 2026", hours=4.0, amount=50.0),
    ]
    metrics, _ = build_period_metrics(entries, date(2026, 5, 11), date(2026, 5, 17))
    assert metrics["hours"] == 4.0
    assert metrics["entries_count"] == 1


def test_bad_dates_excluded_and_counted():
    entries = [
        _entry(date="bogus", hours=9.0, amount=99.0),
        _entry(date="May 13, 2026", hours=1.0, amount=12.5),
    ]
    metrics, unparsed = build_period_metrics(entries, date(2026, 5, 11), date(2026, 5, 17))
    assert unparsed == 1
    assert metrics["hours"] == 1.0


def test_scope_risk_sums_over_scope_amounts():
    entries = [
        _entry(hours=2.0, amount=25.0, ai_classification={"is_over_scope": True}),
        _entry(hours=2.0, amount=25.0, ai_classification={"is_over_scope": False}),
    ]
    metrics, _ = build_period_metrics(entries, date(2026, 5, 11), date(2026, 5, 17))
    assert metrics["scope_risk_usd"] == 25.0


def test_contractor_filter():
    entries = [
        _entry(contractor="A", hours=1.0, amount=10.0),
        _entry(contractor="B", hours=8.0, amount=80.0),
    ]
    metrics, _ = build_period_metrics(
        entries, date(2026, 5, 11), date(2026, 5, 17), contractor_filter="A"
    )
    assert metrics["hours"] == 1.0
    assert metrics["active_contractors"] == 1
