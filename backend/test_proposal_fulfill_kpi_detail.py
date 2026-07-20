"""Tests for KPI detail repair (post label-swap cleanup)."""

from app.services.proposal_fulfill_kpi_detail import (
    content_has_kpi_detail_artifacts,
    repair_kpi_detail_artifacts,
)
from app.services.proposal_fulfill_kpi_fix import apply_contractor_kpi_text_fixes


def test_no_longer_label_swaps_sentiment_to_arrivals():
    text = "Resident Sentiment Survey — favorable rating among residents toward tourism."
    fixed, _ = apply_contractor_kpi_text_fixes(text)
    assert "Total Visitor Arrivals (contractor KPI)" not in fixed


def test_detects_fabricated_arrivals_survey():
    text = (
        "Total Visitor Arrivals (contractor KPI) Survey — favorable rating among "
        "Hawaiʻi residents toward tourism."
    )
    assert content_has_kpi_detail_artifacts(text)
    fixed, logs = repair_kpi_detail_artifacts(text)
    assert "favorable rating among" not in fixed.casefold()
    assert "airline passenger" in fixed.casefold() or "PAX" in fixed
    assert logs


def test_dedupes_double_kpi_intro():
    text = (
        "three contractor KPIs under Section 2.3: Total Visitor Arrivals (+3.0% annual growth), "
        "Total Visitor Expenditures (+4.6% annual growth), and Average Islands Visited Per Person "
        "(+0.8% annual growth) — three Key Performance Indicators (Total Visitor Arrivals, "
        "Total Visitor Expenditures, and Average Islands Visited Per Person, with the Section 2.3 "
        "annual growth targets)."
    )
    fixed, logs = repair_kpi_detail_artifacts(text)
    assert fixed.count("Total Visitor Arrivals") >= 1
    assert "three Key Performance Indicators" not in fixed or logs
