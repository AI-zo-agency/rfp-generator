"""Evidence assembly and prompt construction. No network.

The bugs pinned here all came out of live calls, not imagination: month labels
printed as "Jan 2024 2024", the cash forecast overran its token budget mid-JSON,
and the model invented an outflow rate 27% below the measured one because the
prompt never gave it any outflow history.
"""

from datetime import date

import pytest

from app.financial import qb_forecast_llm as F

_AS_OF = date(2026, 9, 3)


def _overview(**over):
    base = {
        "liquidity": {"cash": 28_959.0, "net_cash_change": 27_592.0},
        "monthly_trend": {"months": [
            {"month": "Jan 2026", "amount": 172_528.0, "cost_of_services": 64_266.0},
            {"month": "Feb 2026", "amount": 140_083.0, "cost_of_services": 71_435.0},
            {"month": "Mar 2026", "amount": 0.0},
        ]},
        "billing_vs_cash": {"by_month": [
            {"month": "Jan", "invoiced": 172_528.0, "collected": 99_557.0},
            {"month": "Feb", "invoiced": 140_083.0, "collected": 138_357.0},
        ]},
        "purchase_orders": {"open_total": 132_620.0},
        "cost_completeness": {"curve": [{"days": 2, "pct": 61.8}],
                              "unsettled_months": ["Aug 2026"]},
    }
    return {**base, **over}


@pytest.fixture
def repo(monkeypatch):
    """Empty mirror by default; each test installs only what it needs."""
    state = {"invoices": [], "payments": [], "links": [], "bills": [],
             "bill_payments": [], "purchases": [], "caches": {}}
    monkeypatch.setattr(F.repo, "list_invoices", lambda r, **k: state["invoices"])
    monkeypatch.setattr(F.repo, "list_payments", lambda r, **k: state["payments"])
    monkeypatch.setattr(F.repo, "list_txn_links", lambda r, **k: state["links"])
    monkeypatch.setattr(F.repo, "list_bills", lambda r, **k: state["bills"])
    monkeypatch.setattr(F.repo, "list_bill_payments", lambda r, **k: state["bill_payments"])
    monkeypatch.setattr(F.repo, "list_purchases", lambda r, **k: state["purchases"])
    monkeypatch.setattr(F.repo, "get_panel_cache", lambda r, y: state["caches"].get(y))
    return state


# ── month labels ─────────────────────────────────────────────────────────────

def test_month_label_is_not_double_stamped_with_the_year():
    """The P&L column already reads "Jan 2026". The first live prompt shipped
    "Jan 2024 2024" to the model."""
    assert F._label("Jan 2026", 2026) == "Jan 2026"
    assert F._label("Jan", 2026) == "Jan 2026"
    assert F._label("Jan", None) == "Jan"


def test_history_spans_prior_years_and_drops_unstarted_months(repo):
    repo["caches"][2025] = {"payload": {"monthly_trend": {"months": [
        {"month": "Dec 2025", "amount": 79_808.0, "cost_of_services": 69_115.0}]}}}
    evidence = F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF)
    labels = [m["month"] for m in evidence["months"]]
    # Prior year first, current year after, and March (amount 0) excluded.
    assert labels == ["Dec 2025", "Jan 2026", "Feb 2026"]


def test_missing_prior_year_is_not_fatal(repo):
    evidence = F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF)
    assert [m["month"] for m in evidence["months"]] == ["Jan 2026", "Feb 2026"]


# ── open items ───────────────────────────────────────────────────────────────

def test_open_invoices_are_itemised_with_age(repo):
    repo["invoices"] = [
        {"qbo_id": "1", "customer_name": "Big Co", "txn_date": "2026-08-31",
         "due_date": "2026-09-01", "balance": 40_920.0, "is_deleted": False},
        {"qbo_id": "2", "customer_name": "Paid Co", "txn_date": "2026-08-01",
         "balance": 0.0, "is_deleted": False},
        {"qbo_id": "3", "customer_name": "Void Co", "txn_date": "2026-08-01",
         "balance": 5_000.0, "is_deleted": True},
        {"qbo_id": "4", "customer_name": "Future Co", "txn_date": "2026-12-01",
         "balance": 9_000.0, "is_deleted": False},
    ]
    rows = F.open_invoices("realm", as_of=_AS_OF)
    assert [r["client"] for r in rows] == ["Big Co"]
    assert rows[0]["age_days"] == 3 and rows[0]["overdue_days"] == 2


def test_collection_curve_is_built_from_linked_payments(repo):
    repo["invoices"] = [
        {"qbo_id": "1", "txn_date": "2026-01-01", "balance": 0.0, "is_deleted": False},
        {"qbo_id": "2", "txn_date": "2026-01-01", "balance": 0.0, "is_deleted": False},
    ]
    repo["payments"] = [
        {"qbo_id": "p1", "txn_date": "2026-01-11", "is_deleted": False},
        {"qbo_id": "p2", "txn_date": "2026-02-15", "is_deleted": False},
    ]
    repo["links"] = [
        {"from_id": "p1", "to_id": "1", "to_type": "Invoice", "amount": 100.0},
        {"from_id": "p2", "to_id": "2", "to_type": "Invoice", "amount": 100.0},
    ]
    curve = F.collection_curve("realm")
    assert curve["sample_invoices"] == 2
    assert curve["cumulative_pct"]["14"] == 50.0   # only the 10-day one has landed
    assert curve["cumulative_pct"]["90"] == 100.0


def test_collection_curve_is_none_without_links(repo):
    assert F.collection_curve("realm") is None


# ── the outflow caveat ───────────────────────────────────────────────────────

def _with_outflow(repo, months):
    repo["purchases"] = [
        {"txn_date": f"{m}-15", "total_amt": amt, "is_deleted": False}
        for m, amt in months
    ]


def test_outflow_caveat_excludes_the_month_in_progress(repo):
    """Nine months of spend divided by nine, when the ninth is three days old,
    put the baseline 9% low on the first live call."""
    _with_outflow(repo, [("2026-01", 100_000.0), ("2026-02", 100_000.0),
                         ("2026-09", 3_000.0)])
    evidence = F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF)
    caveat = F._outflow_caveat(evidence)
    assert "2 complete months" in caveat
    # 237,914 collected - 27,592 net change = 210,322 over 2 months.
    assert "105,161 per month" in caveat


def test_outflow_caveat_states_the_overstatement(repo):
    # Ledger rows must exceed implied real outflow for the overstatement branch;
    # 210,322 is implied here, so 260,000 of rows runs ~24% high.
    _with_outflow(repo, [("2026-01", 130_000.0), ("2026-02", 130_000.0)])
    caveat = F._outflow_caveat(
        F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF)
    )
    assert "overstate" in caveat and "% high" in caveat


def test_outflow_caveat_degrades_when_cash_flow_is_unknown(repo):
    _with_outflow(repo, [("2026-01", 100_000.0), ("2026-02", 100_000.0)])
    overview = _overview(liquidity={"cash": 1.0, "net_cash_change": None})
    caveat = F._outflow_caveat(
        F.build_evidence("realm", overview, year=2026, as_of=_AS_OF)
    )
    assert "upper bound" in caveat


# ── prompts ──────────────────────────────────────────────────────────────────

def test_cash_prompt_carries_the_evidence_the_model_needs(repo):
    repo["invoices"] = [{"qbo_id": "1", "customer_name": "Big Co",
                         "txn_date": "2026-08-31", "due_date": "2026-09-01",
                         "balance": 40_920.0, "is_deleted": False}]
    _with_outflow(repo, [("2026-01", 130_000.0), ("2026-02", 130_000.0)])
    prompt = F.cash_prompt(F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF))
    for expected in ("Big Co", "28,959", "132,620", "13 weeks", "overstate"):
        assert expected in prompt


def test_year_prompt_states_booked_months_and_asks_for_the_total(repo):
    prompt = F.year_prompt(
        F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF), year=2026
    )
    assert "2 of 12 months booked" in prompt
    assert "Months remaining to forecast: 10" in prompt
    assert "312,611" in prompt   # Jan + Feb income


def test_prompts_do_not_hand_over_derived_growth_rates(repo):
    """Supplying computed year-over-year growth made every model tested worse.
    The prompts give rows; the model picks its own method."""
    evidence = F.build_evidence("realm", _overview(), year=2026, as_of=_AS_OF)
    both = F.cash_prompt(evidence) + F.year_prompt(evidence, year=2026)
    for banned in ("year-over-year", "YoY", "growth rate", "prior year remaining"):
        assert banned.lower() not in both.lower()


def test_cash_forecast_gets_a_larger_token_budget_than_the_year():
    """13 week objects plus reasoning overran 4096 on the first live call and
    came back as empty content rather than an error."""
    assert F._CASH_MAX_TOKENS > F._MAX_TOKENS
