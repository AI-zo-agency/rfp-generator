from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.financial.iworker_period_insights import (
    build_period_insights,
    build_period_metrics,
    expected_hours_for_period,
    month_bounds,
    month_label,
    parse_entry_date,
    week_bounds,
    week_label,
)

PT = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 5, 13, 15, 0, tzinfo=PT)


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


def test_wow_compares_to_previous_full_week():
    entries = [
        _entry(date="May 13, 2026", hours=10.0, amount=100.0),
        _entry(date="May 6, 2026", hours=8.0, amount=80.0),
    ]
    out = build_period_insights(entries, granularity="week", now=NOW)
    assert out["selected"]["start"] == "2026-05-11"
    assert out["selected"]["end"] == "2026-05-17"
    assert out["selected"]["is_current"] is True
    assert out["current"]["hours"] == 10.0
    assert out["previous_metrics"]["hours"] == 8.0
    assert out["delta"]["hours_pct"] == 25.0


def test_mom_compares_to_previous_full_month():
    entries = [
        _entry(date="May 5, 2026", hours=4.0, amount=40.0),
        _entry(date="April 10, 2026", hours=8.0, amount=80.0),
    ]
    out = build_period_insights(entries, granularity="month", now=NOW)
    assert out["selected"]["start"] == "2026-05-01"
    assert out["current"]["hours"] == 4.0
    assert out["previous_metrics"]["hours"] == 8.0
    assert out["delta"]["hours_pct"] == -50.0


def test_null_previous_yields_null_deltas():
    entries = [_entry(date="May 13, 2026", hours=2.0, amount=20.0)]
    out = build_period_insights(entries, granularity="week", now=NOW)
    assert out["previous_metrics"]["hours"] == 0.0
    assert out["delta"]["hours_pct"] is None


def test_period_start_snaps_to_monday():
    entries = [_entry(date="May 6, 2026", hours=3.0, amount=30.0)]
    out = build_period_insights(
        entries, granularity="week", period_start="2026-05-07", now=NOW
    )
    assert out["selected"]["start"] == "2026-05-04"
    assert out["current"]["hours"] == 3.0


def test_contractor_rows_include_weekend():
    entries = [
        _entry(contractor="Murilo", date="May 16, 2026", hours=3.0, amount=37.5, rate=12.5),
        _entry(contractor="Murilo", date="May 9, 2026", hours=2.0, amount=25.0, rate=12.5),
    ]
    out = build_period_insights(entries, granularity="week", now=NOW)
    row = next(c for c in out["contractors"] if c["name"] == "Murilo")
    assert row["hours"] == 3.0
    assert row["hours_delta_pct"] == 50.0


def test_expected_hours_prorates_in_progress_week():
    hours = expected_hours_for_period(
        "week",
        date(2026, 5, 11),
        date(2026, 5, 17),
        date(2026, 5, 13),
        20.0,
        None,
        {},
    )
    assert round(hours, 2) == round(20.0 * 3 / 7, 2)


def test_expected_hours_full_closed_week():
    hours = expected_hours_for_period(
        "week",
        date(2026, 5, 4),
        date(2026, 5, 10),
        date(2026, 5, 13),
        20.0,
        None,
        {},
    )
    assert hours == 20.0


def test_underlogged_signal_after_wednesday():
    entries = [_entry(contractor="Murilo", date="May 13, 2026", hours=1.0, amount=12.5)]
    out = build_period_insights(
        entries,
        granularity="week",
        now=NOW,
        expected_hours_by_contractor={"Murilo": 20.0},
    )
    ids = [s["id"] for s in out["signals"]]
    assert "iworker:underlogged:Murilo" in ids


def test_overcapacity_signal():
    entries = [_entry(contractor="Murilo", date="May 13, 2026", hours=20.0, amount=250.0)]
    out = build_period_insights(
        entries,
        granularity="week",
        now=NOW,
        expected_hours_by_contractor={"Murilo": 20.0},
    )
    ids = [s["id"] for s in out["signals"]]
    assert "iworker:overcapacity:Murilo" in ids


def test_spend_spike_and_scope_risk_signals():
    entries = [
        _entry(date="May 13, 2026", hours=10.0, amount=130.0, ai_classification={"is_over_scope": True}),
        _entry(date="May 6, 2026", hours=10.0, amount=100.0),
    ]
    out = build_period_insights(entries, granularity="week", now=NOW)
    ids = [s["id"] for s in out["signals"]]
    assert "iworker:spend_spike" in ids
    assert any(i.startswith("iworker:scope_risk:") for i in ids)


def test_underlogged_signal_uses_month_wording():
    aug_now = datetime(2026, 8, 31, 15, 0, tzinfo=PT)
    entries = [_entry(contractor="Murilo", date="August 15, 2026", hours=5.0, amount=62.5)]
    out = build_period_insights(
        entries,
        granularity="month",
        period_start="2026-08-01",
        now=aug_now,
        expected_hours_by_contractor={"Murilo": 20.0},
    )
    under = next(s for s in out["signals"] if s["id"] == "iworker:underlogged:Murilo")
    assert "this month" in under["headline"].lower()
    assert "this week" not in under["headline"].lower()


def test_month_includes_weekly_contractor_breakdown():
    aug_now = datetime(2026, 8, 31, 15, 0, tzinfo=PT)
    entries = [
        _entry(contractor="Murilo", date="August 5, 2026", hours=4.0, amount=50.0),
        _entry(contractor="Marcelle Benevides", date="August 12, 2026", hours=6.0, amount=84.0),
    ]
    out = build_period_insights(
        entries,
        granularity="month",
        period_start="2026-08-01",
        now=aug_now,
    )
    weeks = out["weekly_in_month"]
    assert len(weeks) >= 4
    aug4 = next(w for w in weeks if w["start"] == "2026-08-03")
    assert aug4["total_hours"] == 4.0
    assert aug4["contractors"][0]["name"] == "Murilo"
    aug11 = next(w for w in weeks if w["start"] == "2026-08-10")
    assert aug11["contractors"][0]["hours"] == 6.0
