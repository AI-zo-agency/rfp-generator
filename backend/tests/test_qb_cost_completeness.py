"""Fixtures mirror the real ledger's behaviour: bills arrive late, so a recent
month's margin reads high. The August case is the one that motivated the panel —
$29,116 of cost against a ~$70k norm, showing as a record 68.9% margin.
"""

from datetime import date, timedelta

import pytest

from app.financial import qb_cost_completeness as cc

_AS_OF = date(2026, 9, 2)


def _seed_costs():
    """Two years of monthly cost, each month arriving on a realistic lag.

    60% lands within two days of month end, the rest spread out to ~50 days, so
    the fitted curve reproduces the shape measured on the live ledger.
    """
    rows = []
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            if (year, month) > (2026, 8):
                continue
            end = cc._month_end(year, month - 1)
            for lag, amount in ((-10, 42_000.0), (2, 18_000.0), (20, 6_000.0), (50, 4_000.0)):
                seen = end + timedelta(days=lag)
                rows.append((end - timedelta(days=14), max(seen, end - timedelta(days=14)), amount))
    return rows


def _trend(months):
    return {"months": [
        {"month": m, "amount": inc, "cost_of_services": cost} for m, inc, cost in months
    ]}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(cc, "_cost_rows", lambda realm_id, since: _seed_costs())


def _run(months):
    return cc.cost_completeness(
        "realm", 2026, as_of=_AS_OF, monthly_trend=_trend(months)
    )


_JAN_TO_JUL = [
    ("Jan 2026", 172_528.02, 64_266.03), ("Feb 2026", 140_083.16, 71_434.61),
    ("Mar 2026", 213_212.01, 72_659.99), ("Apr 2026", 176_880.15, 100_186.43),
    ("May 2026", 91_146.16, 49_269.35), ("Jun 2026", 127_644.66, 87_376.21),
    ("Jul 2026", 101_829.26, 52_044.52),
]
_AUGUST = ("Aug 2026", 93_515.03, 29_116.30)


def test_recent_months_are_not_settled(patched):
    """August is 2 days old and July 33, and on this lag curve neither has all
    its cost. Settled lags by roughly a quarter — that is the honest cost of a
    95% bar, not a bug. At 90% a half-cost business still reads ~5 margin points
    high, which is the distortion the panel exists to stop."""
    out = _run([*_JAN_TO_JUL, _AUGUST])
    by = {m["month"]: m for m in out["months"]}
    assert by["Aug 2026"]["settled"] is False
    assert by["Aug 2026"]["age_days"] == 2
    assert by["Jul 2026"]["settled"] is False
    assert out["unsettled_months"] == ["Jul 2026", "Aug 2026"]
    assert out["settled_through"] == "Jun 2026"


def test_adjusted_margin_is_lower_than_reported(patched):
    """The whole point: grossing up the missing cost must pull margin down."""
    out = _run([*_JAN_TO_JUL, _AUGUST])
    assert out["adjusted_gross_margin_pct"] < out["reported_gross_margin_pct"]
    assert out["overstated_points"] > 0
    assert out["missing_cost"] > 0


def test_no_cross_period_margin_is_exposed(patched):
    """A settled-months-only margin covers a different span and on real data came
    out higher than reported. Shipping it beside the others invites quoting a real
    number about the wrong months, so it must stay absent."""
    assert "settled_gross_margin_pct" not in _run([*_JAN_TO_JUL, _AUGUST])


def test_expected_cost_never_below_booked(patched):
    """Grossing up may only add cost. A curve dip that inverted this would
    silently improve margin, which is the exact failure being guarded."""
    out = _run([*_JAN_TO_JUL, _AUGUST])
    for m in out["months"]:
        assert m["expected_cost"] >= m["booked_cost"] - 0.01


def test_curve_is_monotonic(patched):
    out = _run([*_JAN_TO_JUL, _AUGUST])
    pcts = [p["pct"] for p in out["curve"]]
    assert pcts == sorted(pcts)
    assert pcts[-1] <= 100.0


def test_degrades_to_none_without_enough_history(monkeypatch):
    monkeypatch.setattr(cc, "_cost_rows", lambda realm_id, since: _seed_costs()[:8])
    assert _run([*_JAN_TO_JUL, _AUGUST]) is None


def test_degrades_to_none_when_trend_has_no_cost_rows(patched):
    trend = {"months": [{"month": "Jan 2026", "amount": 172_528.02}]}
    assert cc.cost_completeness("realm", 2026, as_of=_AS_OF, monthly_trend=trend) is None
    assert cc.cost_completeness("realm", 2026, as_of=_AS_OF, monthly_trend=None) is None


def test_month_end_handles_december():
    assert cc._month_end(2026, 11) == date(2026, 12, 31)
    assert cc._month_end(2026, 0) == date(2026, 1, 31)
    assert cc._month_end(2024, 1) == date(2024, 2, 29)
