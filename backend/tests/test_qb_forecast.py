"""The EverFast case is the fixture: $30k on a monthly rhythm for six months,
then silence. It is the alert this module exists for, and it is also the case
that breaks a naive cadence calculation, because that client raises several
invoices on separate days within one month.
"""

from datetime import date

import pytest

from app.financial import qb_forecast as fc

_AS_OF = date(2026, 9, 3)


def _inv(customer, txn, amount=30_000.0, deleted=False):
    return {
        "customer_name": customer, "txn_date": txn.isoformat(),
        "total_amt": amount, "is_deleted": deleted,
    }


def _monthly(dates, customer, amount=30_000.0):
    return [_inv(customer, d, amount) for d in dates]


def _everfast():
    """Monthly on roughly the 10th, last invoice 12 July — 53 days before as_of.

    Two extra same-month invoices in the older history reproduce the real
    pattern that pulls a whole-history median gap down to a fortnight.
    """
    dates = [date(2026, m, 12) for m in range(2, 8)]
    dates += [date(2025, 11, 12), date(2025, 11, 20), date(2025, 12, 8)]
    return _monthly(sorted(dates), "EverFast Fiber")


def _steady(customer, last_month=9, amount=20_000.0):
    """A client still billing on time, ending in the current month."""
    return _monthly([date(2026, m, 2) for m in range(3, last_month + 1)], customer, amount)


@pytest.fixture
def patched(monkeypatch):
    def _install(invoices):
        monkeypatch.setattr(fc.repo, "list_invoices", lambda realm_id, **kw: invoices)
    return _install


def test_monthly_retainer_gone_quiet_is_flagged(patched):
    patched(_everfast())
    out = fc.billing_gaps("realm", as_of=_AS_OF)
    assert [c["client"] for c in out["clients"]] == ["EverFast Fiber"]
    row = out["clients"][0]
    assert row["days_silent"] == 53
    # Cadence must read as monthly. The same-month invoices in older history
    # would drag a whole-history median to ~14 days and understate the gap.
    assert 20 <= row["typical_gap_days"] <= 35


def test_client_still_billing_on_time_is_not_flagged(patched):
    patched(_steady("Regular Co"))
    assert fc.billing_gaps("realm", as_of=_AS_OF)["clients"] == []


def test_long_churned_client_is_not_flagged(patched):
    """Silence past four cycles is history, not an alert. Leaving it in buries
    the retainer that stopped last month under departures already known about."""
    old = _monthly([date(2025, m, 5) for m in range(1, 7)], "Long Gone Ltd")
    patched(old + _everfast())
    assert [c["client"] for c in fc.billing_gaps("realm", as_of=_AS_OF)["clients"]] == [
        "EverFast Fiber"
    ]


def test_small_and_short_history_clients_are_ignored(patched):
    tiny = _monthly([date(2026, m, 4) for m in range(2, 8)], "Tiny Co", amount=100.0)
    brief = _monthly([date(2026, 2, 4), date(2026, 3, 4)], "Two Invoices Co", 50_000.0)
    patched(tiny + brief)
    assert fc.billing_gaps("realm", as_of=_AS_OF)["clients"] == []


def test_deleted_and_future_invoices_are_excluded(patched):
    rows = _everfast()
    rows.append(_inv("EverFast Fiber", date(2026, 9, 1), deleted=True))
    rows.append(_inv("EverFast Fiber", date(2026, 12, 1)))
    patched(rows)
    # Neither the void nor the post-dated invoice may reset the silence clock.
    assert fc.billing_gaps("realm", as_of=_AS_OF)["clients"][0]["days_silent"] == 53


def test_flagged_clients_rank_by_revenue_at_risk(patched):
    big = _everfast()
    small = _monthly([date(2026, m, 6) for m in range(2, 8)], "Small Co", 1_500.0)
    patched(big + small)
    out = fc.billing_gaps("realm", as_of=_AS_OF)
    revenues = [c["trailing_12mo_revenue"] for c in out["clients"]]
    assert revenues == sorted(revenues, reverse=True)


# ── projections ──────────────────────────────────────────────────────────────

def _trend(values):
    return {"months": [{"month": f"M{i}", "amount": v} for i, v in enumerate(values)]}


def test_year_projection_scales_booked_months_to_twelve():
    out = fc.year_projection(_trend([100.0] * 8), year=2026)
    assert out["months_booked"] == 8
    assert out["point"] == pytest.approx(1200.0)


def test_year_projection_tightens_once_most_of_the_year_is_booked():
    early = fc.year_projection(_trend([100.0] * 5), year=2026)
    late = fc.year_projection(_trend([100.0] * 10), year=2026)
    assert late["expected_error_pct"] < early["expected_error_pct"]


def test_year_projection_ignores_unstarted_months():
    """A trailing zero is a month that has not happened, not a month of nil."""
    assert fc.year_projection(_trend([100.0] * 8 + [0.0] * 4), year=2026)["months_booked"] == 8


def test_quarter_needs_a_year_of_history():
    assert fc.quarter_projection([100.0] * 11) is None
    assert fc.quarter_projection([100.0] * 12) is not None


def test_quarter_uses_median_so_one_spike_does_not_carry_it():
    flat = fc.quarter_projection([100.0] * 12)
    spiked = fc.quarter_projection([100.0] * 11 + [10_000.0])
    assert spiked["point"] == flat["point"]


def test_quarter_is_marked_low_confidence():
    """~19% error. A caller has to opt in to showing it."""
    out = fc.quarter_projection([100.0] * 12)
    assert out["low_confidence"] is True
    assert out["expected_error_pct"] > 15


def test_no_month_forecast_is_produced(patched, monkeypatch):
    patched([])
    monkeypatch.setattr(fc, "multi_year_income", lambda *a, **k: [100.0] * 12)
    out = fc.forecast("realm", 2026, as_of=_AS_OF, monthly_trend=_trend([100.0] * 8))
    assert out["month"] is None
    assert "19.8%" in out["month_omitted_reason"]
