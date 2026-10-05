from datetime import date

import pytest

from app.financial import hs_forecast as F


def _deal(**kw):
    base = {
        "hs_id": 1,
        "dealname": "X",
        "amount": 10000.0,
        "dealstage": "s",
        "pipeline": "default",
        "closedate": date(2026, 10, 15),
        "hs_is_closed": False,
        "hs_is_closed_won": False,
        "hs_is_closed_lost": False,
        "stage_probability": 0.5,
        "company_hs_id": 9,
    }
    base.update(kw)
    return base


def test_closed_lost_excluded():
    deals = [_deal(hs_is_closed_lost=True, hs_is_closed=True, stage_probability=0)]
    assert F.weighted_contribution(deals[0], stages={}) == 0.0


def test_open_uses_hubspot_probability():
    assert F.weighted_contribution(_deal(stage_probability=0.5), stages={}) == 5000.0


def test_missing_probability_uses_fallback_from_stage_catalog():
    deal = _deal(stage_probability=None, dealstage="late1")
    stages = {
        ("default", "late1"): {
            "probability": None,
            "label": "Finalizing terms",
            "is_closed": False,
        }
    }
    assert F.weighted_contribution(deal, stages) == 8000.0


def test_monthly_hybrid_locks_past_months_to_qb_only():
    as_of = date(2026, 10, 5)
    qb = {"2026-01": 100.0, "2026-10": 50.0}
    deals = [_deal(closedate=date(2026, 1, 10), amount=999, stage_probability=1.0)]
    months = F.monthly_points(qb_booked=qb, deals=deals, stages={}, as_of=as_of, year=2026)
    assert months["2026-01"]["point"] == 100.0
    assert months["2026-10"]["point"] == 50.0 + 999.0


def test_year_equals_sum_of_months():
    as_of = date(2026, 10, 5)
    qb = {f"2026-{m:02d}": 10.0 for m in range(1, 10)}
    deals = [_deal(closedate=date(2026, 11, 1), amount=1000, stage_probability=0.5)]
    months = F.monthly_points(qb_booked=qb, deals=deals, stages={}, as_of=as_of, year=2026)
    year = F.year_point(months)
    assert year == sum(m["point"] for m in months.values())


def test_new_billing_lands_after_invoice_lag_and_collection_median():
    as_of = date(2026, 10, 5)
    deals = [
        _deal(
            closedate=date(2026, 10, 6),
            amount=10000,
            stage_probability=1.0,
            hs_is_closed_won=True,
            hs_is_closed=True,
        )
    ]
    curve = {"median_days": 14, "cumulative_pct": {"7": 40.0, "14": 70.0, "30": 90.0}}
    weeks = F.cash_weeks(
        as_of=as_of,
        cash_on_hand=1000.0,
        open_ar=[{"balance": 500.0, "raised": "2026-09-01", "due": "2026-09-15"}],
        deals=deals,
        stages={},
        collection_curve=curve,
        weekly_outflow=100.0,
    )
    assert len(weeks) == 13
    assert sum(w["from_new_billing"] for w in weeks) > 0
    assert weeks[0]["from_new_billing"] == 0.0


def test_face_amount_never_enters_unweighted():
    deals = [_deal(amount=2_400_000, stage_probability=0.1)]
    weeks = F.cash_weeks(
        as_of=date(2026, 10, 5),
        cash_on_hand=0,
        open_ar=[],
        deals=deals,
        stages={},
        collection_curve={"median_days": 0, "cumulative_pct": {"0": 100.0}},
        weekly_outflow=0,
    )
    assert sum(w["from_new_billing"] for w in weeks) == pytest.approx(240_000.0)


def test_matched_won_excluded_from_monthly_and_cash():
    as_of = date(2026, 10, 5)
    won = _deal(
        hs_id=42,
        amount=20000,
        stage_probability=1.0,
        hs_is_closed=True,
        hs_is_closed_won=True,
        closedate=date(2026, 10, 20),
    )
    months = F.monthly_points(
        qb_booked={"2026-10": 0.0},
        deals=[won],
        stages={},
        as_of=as_of,
        year=2026,
        matched_won_ids={42},
    )
    assert months["2026-10"]["won_awaiting_invoice"] == 0.0
    weeks = F.cash_weeks(
        as_of=as_of,
        cash_on_hand=0,
        open_ar=[],
        deals=[won],
        stages={},
        collection_curve={"median_days": 0, "cumulative_pct": {"0": 100.0}},
        weekly_outflow=0,
        matched_won_ids={42},
    )
    assert sum(w["from_new_billing"] for w in weeks) == 0.0


def test_zero_amount_and_missing_close_contribute_nothing():
    as_of = date(2026, 10, 5)
    deals = [
        _deal(amount=0, stage_probability=1.0),
        _deal(hs_id=2, amount=5000, closedate=None, stage_probability=1.0),
    ]
    months = F.monthly_points(
        qb_booked={}, deals=deals, stages={}, as_of=as_of, year=2026
    )
    assert F.year_point(months) == 0.0
    weeks = F.cash_weeks(
        as_of=as_of,
        cash_on_hand=100,
        open_ar=[],
        deals=deals,
        stages={},
        collection_curve={"median_days": 0, "cumulative_pct": {"0": 100.0}},
        weekly_outflow=0,
    )
    # Missing close falls back to as_of → can land in horizon at lag 0
    assert weeks[0]["closing_balance"] >= 100


def test_deal_beyond_13_weeks_excluded_from_cash():
    as_of = date(2026, 10, 5)
    deals = [_deal(closedate=date(2027, 3, 1), amount=50000, stage_probability=1.0)]
    weeks = F.cash_weeks(
        as_of=as_of,
        cash_on_hand=0,
        open_ar=[],
        deals=deals,
        stages={},
        collection_curve={"median_days": 0, "cumulative_pct": {"0": 100.0}},
        weekly_outflow=0,
    )
    assert sum(w["from_new_billing"] for w in weeks) == 0.0


def test_probability_above_one_is_clamped():
    assert F.weighted_contribution(_deal(stage_probability=1.5), stages={}) == 10000.0


def test_unmatched_won_with_past_close_rolls_into_as_of_month():
    as_of = date(2026, 10, 5)
    won = _deal(
        hs_id=9,
        amount=25000,
        stage_probability=1.0,
        hs_is_closed=True,
        hs_is_closed_won=True,
        closedate=date(2026, 3, 15),
    )
    months = F.monthly_points(
        qb_booked={"2026-03": 100.0, "2026-10": 0.0},
        deals=[won],
        stages={},
        as_of=as_of,
        year=2026,
        matched_won_ids=set(),
    )
    assert months["2026-03"]["point"] == 100.0  # QB only, no HubSpot rewrite
    assert months["2026-10"]["won_awaiting_invoice"] == 25000.0
    assert F.year_point(months) == 100.0 + 25000.0
