"""Tests for deterministic contractor KPI fixes (HTA Section 2.3)."""

from app.services.proposal_fulfill_kpi_fix import apply_contractor_kpi_text_fixes


def test_replaces_four_kpi_enumeration():
    text = (
        "HTA's contract holds us accountable to four headline KPIs: "
        "Resident Sentiment, Visitor Satisfaction, Average Daily Visitor Spending, "
        "and Total Visitor Expenditures."
    )
    fixed, logs = apply_contractor_kpi_text_fixes(text)
    assert "Resident Sentiment" not in fixed
    assert "four headline KPI" not in fixed.casefold()
    assert "Total Visitor Arrivals" in fixed
    assert logs


def test_signature_certification_block():
    text = (
        "four Key Performance Indicators (Resident Sentiment, Visitor Satisfaction, "
        "Average Daily Visitor Spending, and Total Visitor Expenditures)."
    )
    fixed, _ = apply_contractor_kpi_text_fixes(text)
    assert "Resident Sentiment" not in fixed
    assert "Average Islands Visited" in fixed
