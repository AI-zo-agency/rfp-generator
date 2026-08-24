"""Figures are the live 2026-08-24 position, so a payload shape change fails
here rather than surfacing as an odd number on the dashboard."""

from app.financial.qb_position import position


def _live() -> dict:
    return {
        "liquidity": {"cash": 7_742.33, "as_of": "2026-08-24"},
        "ap": {
            "total": 38_643.22,
            "buckets": [
                {"label": "Not yet due", "amount": 11_670.58},
                {"label": "1-30 days", "amount": 16_020.40},
                {"label": "31-60 days", "amount": 10_952.24},
                {"label": "61-90 days", "amount": 0.0},
                {"label": "90+ days", "amount": 0.0},
            ],
        },
        "ar": {
            "total": 51_244.06,
            "overdue_total": 28_498.23,
            "buckets": [
                {"label": "Not yet due", "amount": 22_745.83},
                {"label": "1-30 days", "amount": 13_331.83},
                {"label": "31-60 days", "amount": 13_966.40},
                {"label": "61-90 days", "amount": 1_200.00},
                {"label": "90+ days", "amount": 0.0},
            ],
        },
    }


def test_the_live_position_nets_positive_despite_the_headline_ratio():
    """Payables are 4.99x cash, which the first brief called a crunch. Cash plus
    overdue receivables minus overdue payables is +$9,268."""
    out = position(_live())
    assert out["cash_figure"] == "$7,742"
    assert out["overdue_ap_figure"] == "$26,973"
    assert out["overdue_ar_figure"] == "$28,498"
    assert out["net_figure"] == "$9,268"
    assert round(out["net_amount"], 2) == 9_267.92


def test_not_yet_due_is_excluded_from_both_sides():
    out = position(_live())
    # $11,671 of bills and $22,746 of invoices are not yet due; neither counts.
    assert out["overdue_ap_figure"] != "$38,643"
    assert out["overdue_ar_figure"] != "$51,244"


def test_missing_liquidity_hides_the_strip_rather_than_rendering_a_hole():
    data = _live()
    data.pop("liquidity")
    assert position(data) is None
    assert position({}) is None
    assert position({"liquidity": {}}) is None


def test_a_real_zero_cash_still_produces_a_strip():
    """Zero is a fact worth showing; only a missing figure hides the strip."""
    data = _live() | {"liquidity": {"cash": 0}}
    out = position(data)
    assert out is not None
    assert out["cash_figure"] == "$0"
    assert out["net_figure"] == "$1,526"


def test_overdue_total_of_zero_is_used_as_is_not_fallen_back_from():
    """`or` here would sum the buckets and report $28,498 of overdue AR when the
    panel says there is none — the same trap coverage_gap documents."""
    data = _live()
    data["ar"]["overdue_total"] = 0
    out = position(data)
    assert out["overdue_ar_figure"] == "$0"


def test_a_missing_overdue_total_falls_back_to_the_buckets():
    data = _live()
    data["ar"].pop("overdue_total")
    assert position(data)["overdue_ar_figure"] == "$28,498"


def test_a_negative_net_carries_its_sign():
    data = _live()
    data["liquidity"]["cash"] = 100
    data["ar"]["overdue_total"] = 0
    out = position(data)
    assert out["net_figure"] == "-$26,873"
    assert out["net_amount"] < 0


def test_missing_ap_and_ar_panels_leave_cash_standing_alone():
    out = position({"liquidity": {"cash": 7_742.33}})
    assert out["overdue_ap_figure"] == "$0"
    assert out["overdue_ar_figure"] == "$0"
    assert out["net_figure"] == "$7,742"
