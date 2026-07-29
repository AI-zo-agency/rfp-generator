"""Tests for pipeline step-trace helpers (no I/O beyond in-memory classify)."""

from __future__ import annotations

from app.core.step_debug_logger import (
    classify_section_outcome,
    summarize_budget,
    summarize_sections,
)
from app.models.proposal import BudgetLineItem, ProposalBudget


def test_classify_section_outcome_buckets() -> None:
    assert classify_section_outcome("") == "empty"
    assert (
        classify_section_outcome(
            "[VERIFY: Section drafting failed — needs manual regeneration]"
        )
        == "draft_failed"
    )
    assert (
        classify_section_outcome(
            "[VERIFY: Draft content for Foo — insufficient evidence in corpus.]"
        )
        == "insufficient_evidence"
    )
    assert classify_section_outcome("[VERIFY: GET license id]") == "verify_stub"
    long_ok = "We deliver WordPress reports with ADA-compliant patterns. " * 20
    assert classify_section_outcome(long_ok) == "ok"


def test_summarize_sections_counts_problems() -> None:
    sections = [
        {"id": "a", "title": "A", "content": "Solid narrative about our approach. " * 10},
        {
            "id": "b",
            "title": "B",
            "content": "[VERIFY: Section drafting failed — needs manual regeneration]",
        },
        {"id": "c", "title": "C", "content": ""},
    ]
    summary = summarize_sections(sections)
    assert summary["section_count"] == 3
    assert summary["ok_count"] == 1
    assert summary["problem_count"] == 2
    assert "b" in summary["problem_ids"]
    assert "c" in summary["problem_ids"]
    assert summary["by_outcome"]["draft_failed"] == 1
    assert summary["by_outcome"]["empty"] == 1


def test_summarize_budget_binding() -> None:
    budget = ProposalBudget(
        rfpId="rfp-1",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[
            BudgetLineItem(
                id="L1",
                description="Bound",
                category="labor",
                extended=1000,
                sourceRateId="rate-1",
                isManualFill=False,
            ),
            BudgetLineItem(
                id="L2",
                description="Manual",
                category="labor",
                extended=0,
                isManualFill=True,
            ),
            BudgetLineItem(
                id="L3",
                description="Unbound",
                category="labor",
                extended=500,
            ),
        ],
        agencyRevenueEstimate=1500,
        lumpSumTotal=1500,
        pricingTier="Average",
        pricingFlags=["[PRICING FLAG: test]"],
    )
    summary = summarize_budget(budget)
    assert summary["present"] is True
    assert summary["line_items"] == 3
    assert summary["bound_lines"] == 1
    assert summary["manual_fill_lines"] == 1
    assert summary["unbound_lines"] == 1
    assert summary["revenue"] == 1500
    assert summary["pricing_flag_count"] == 1
