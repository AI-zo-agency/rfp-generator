"""Fixtures are the real 2025 and 2026 series, so a payload shape change fails
here rather than surfacing as a wrong sentence in a leadership brief."""

from app.financial.qb_trend import margin_rows

# Jan-Aug 2026. August is the current month, still being invoiced on the 24th.
_2026 = [
    ("Jan 2026", 172_528.02, 108_262.0), ("Feb 2026", 140_083.16, 68_649.0),
    ("Mar 2026", 213_212.01, 140_552.0), ("Apr 2026", 176_880.15, 76_694.0),
    ("May 2026", 91_146.16, 42_620.0), ("Jun 2026", 127_644.66, 41_012.0),
    ("Jul 2026", 101_829.26, 50_528.0), ("Aug 2026", 24_613.58, 2_947.0),
]
_2025 = [
    ("Jan 2025", 102_732.0, 33_913.0), ("Feb 2025", 100_087.0, 40_154.0),
    ("Mar 2025", 132_944.0, 67_956.0), ("Apr 2025", 118_545.0, 57_466.0),
    ("May 2025", 63_494.0, -14_553.0), ("Jun 2025", 102_763.0, 40_993.0),
    ("Jul 2025", 102_242.0, 31_578.0), ("Aug 2025", 145_428.0, 70_573.0),
    ("Sep 2025", 109_447.0, 49_210.0), ("Oct 2025", 113_151.0, 45_821.0),
    ("Nov 2025", 108_324.0, 36_992.0), ("Dec 2025", 79_808.0, 10_693.0),
]


def _trend(series, *, with_margin=True):
    months = []
    for name, amount, gross in series:
        entry = {"month": name, "amount": amount}
        if with_margin:
            entry["gross_profit"] = gross
        months.append(entry)
    return {"months": months}


def _current(**extra):
    return {"monthly_trend": _trend(_2026)} | extra


def _prior():
    return {"monthly_trend": _trend(_2025)}


def _row(rows, row_id):
    return next((r for r in rows if r["id"] == row_id), None)


# ── the partial-month trap ───────────────────────────────────────────────────

def test_the_current_month_is_excluded_and_the_range_says_so():
    """August shows $24,614 on the 24th. Against July that reads as a 76%
    collapse; it is three-quarters of a month."""
    row = _row(margin_rows(_current(), _prior()), "margin:revenue-trend")
    assert "Jan-Jul" in row["detail"]
    assert "Aug" not in row["detail"]


def test_revenue_growth_is_measured_over_the_same_months_both_years():
    row = _row(margin_rows(_current(), _prior()), "margin:revenue-trend")
    # Jan-Jul: $1,023,323 against $722,807, up 41.6%.
    assert row["figure"] == "+42%"
    assert "$1,023,323" in row["detail"]
    assert "$722,807" in row["detail"]


def test_the_intra_year_series_alone_would_say_the_opposite():
    """2026 descends from $213,212 in March to $101,829 in July, which reads as
    collapse. Against 2025 it is growth. This is why the prior year is required
    rather than nice to have."""
    intra = [m["amount"] for m in _trend(_2026)["months"][:7]]
    assert intra[-1] < intra[2] / 2  # the misleading read
    row = _row(margin_rows(_current(), _prior()), "margin:revenue-trend")
    assert row["figure"].startswith("+")


def test_the_detail_carries_the_arc_from_first_month_to_last():
    row = _row(margin_rows(_current(), _prior()), "margin:revenue-trend")
    # +68% in January against flat in July is the finding, not the total.
    assert "+68% in Jan" in row["detail"]
    assert "0% in Jul" in row["detail"]


def test_a_flat_month_reads_as_flat_not_as_plus_zero():
    # July 2026 is -0.4% against July 2025.
    row = _row(margin_rows(_current(), _prior()), "margin:revenue-trend")
    assert "+0%" not in row["detail"]


# ── margin ───────────────────────────────────────────────────────────────────

def test_gross_margin_compares_the_same_months_in_points():
    row = _row(margin_rows(_current(), _prior()), "margin:margin-trend")
    # Jan-Jul: 52% against 36%, up 16 points.
    assert row["figure"] == "52%"
    assert "36%" in row["detail"]
    assert "up 16 points" in row["detail"]


def test_margin_row_is_dropped_when_the_snapshot_has_no_gross_profit():
    current = {"monthly_trend": _trend(_2026, with_margin=False)}
    rows = margin_rows(current, _prior())
    assert _row(rows, "margin:margin-trend") is None
    # Revenue still compares — it only needs the income row.
    assert _row(rows, "margin:revenue-trend") is not None


# ── overlap rules ────────────────────────────────────────────────────────────

def test_only_the_overlap_is_compared_when_the_prior_year_is_shorter():
    prior = {"monthly_trend": _trend(_2025[:4])}  # Jan-Apr only
    row = _row(margin_rows(_current(), prior), "margin:revenue-trend")
    assert "Jan-Apr" in row["detail"]


def test_fewer_than_three_overlapping_months_produces_no_trend_rows():
    current = {"monthly_trend": _trend(_2026[:3])}  # Jan-Mar, minus current = 2
    rows = margin_rows(current, _prior())
    assert _row(rows, "margin:revenue-trend") is None
    assert _row(rows, "margin:margin-trend") is None


def test_a_missing_prior_year_drops_only_the_trend_rows():
    current = _current(
        sales_by_customer={"total": 1_000.0, "clients": [{"client": "Solo", "amount": 500.0}]},
    )
    ids = [r["id"] for r in margin_rows(current, None)]
    assert "margin:revenue-trend" not in ids
    assert "margin:concentration" in ids


# ── concentration ────────────────────────────────────────────────────────────

def _sales(top_amount, total=1_047_913.95):
    return {
        "total": total,
        "clients": [
            {"client": "EverFast Fiber", "amount": top_amount},
            {"client": "City of Umatilla - Rock The Locks", "amount": 147_919.33},
            {"client": "City of Carbondale", "amount": 109_687.08},
            {"client": "Oregon-Canadian Forest Products, Inc.", "amount": 108_727.90},
            {"client": "Mt. View Heating", "amount": 47_200.44},
            {"client": "Tail", "amount": 1_000.0},
        ],
    }


def test_concentration_names_the_top_client_and_the_top_five_share():
    row = _row(
        margin_rows(_current(sales_by_customer=_sales(223_000.0)), _prior()),
        "margin:concentration",
    )
    assert row["figure"] == "21%"
    assert "EverFast Fiber" in row["detail"]
    assert "61%" in row["detail"]


def test_concentration_fires_at_twenty_percent_and_not_below():
    total = 1_000_000.0
    quiet = margin_rows(_current(sales_by_customer=_sales(199_000.0, total)), _prior())
    loud = margin_rows(_current(sales_by_customer=_sales(200_000.0, total)), _prior())
    assert _row(quiet, "margin:concentration") is None
    assert _row(loud, "margin:concentration") is not None


def test_concentration_is_silent_without_sales_data():
    assert _row(margin_rows(_current(), _prior()), "margin:concentration") is None


# ── attribution ──────────────────────────────────────────────────────────────

def _attribution_payload(attributed=161_112.30, cost=516_674.12, income=1_047_937.0):
    return {
        "client_profitability": {"attributed_expense": attributed, "clients": []},
        "pl_summary": {"cost_of_services": cost, "income": income},
    }


def test_attribution_states_the_gap_and_the_overstatement_in_points():
    row = _row(margin_rows(_current(**_attribution_payload()), _prior()), "margin:attribution")
    assert row["figure"] == "$355,562"
    assert "31%" in row["detail"]
    assert "34 points" in row["detail"]
    # Load-bearing: without it the model reads the points as a haircut on
    # company gross margin, which already includes this cost.
    assert "Company gross margin already counts this cost" in row["detail"]


def test_attribution_never_emits_a_corrected_per_client_margin():
    """Pro-rata by revenue pulls every client to the company average by
    construction, which manufactures precision. The row is an error bar."""
    row = _row(margin_rows(_current(**_attribution_payload()), _prior()), "margin:attribution")
    assert "cannot be measured" in row["detail"]
    for misleading in ("EverFast", "85%", "51%"):
        assert misleading not in row["detail"]


def test_attribution_is_silent_once_most_cost_is_attributed():
    payload = _attribution_payload(attributed=400_000.0, cost=516_674.12)
    assert _row(margin_rows(_current(**payload), _prior()), "margin:attribution") is None


def test_attribution_is_silent_without_a_cost_of_services_figure():
    payload = _attribution_payload()
    payload["pl_summary"] = {"income": 1_047_937.0}
    assert _row(margin_rows(_current(**payload), _prior()), "margin:attribution") is None


# ── shape ────────────────────────────────────────────────────────────────────

def test_rows_are_ordered_top_line_profit_risk_then_trust():
    rows = margin_rows(
        _current(sales_by_customer=_sales(223_000.0), **_attribution_payload()), _prior()
    )
    assert [r["id"] for r in rows] == [
        "margin:revenue-trend",
        "margin:margin-trend",
        "margin:concentration",
        "margin:attribution",
    ]


def test_an_empty_payload_produces_no_rows():
    assert margin_rows({}, None) == []
    assert margin_rows({}, {}) == []
