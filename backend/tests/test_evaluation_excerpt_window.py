"""The criteria agent must be shown the scoring table, wherever it sits.

Regression: on a live 77k-char RFP the scoring table began at char 40,522 —
522 past the agent's blind ``rfp_context[:40000]`` slice. The model returned
1 of 8 criteria with no point values, which then disarmed every downstream
protection (the cap floor and the scored-tab cap protection both read from
those criteria) and a scored section was silently deleted from the outline.
"""

from __future__ import annotations

from app.services.proposal_intelligence.agents.evaluation_criteria import (
    _evaluation_excerpt,
)

# Shaped like the real failure: filler first, scoring table past the 40k mark.
_TABLE = (
    "SECTION V - RFP RESPONSE REQUIREMENTS AND EVALUATION CRITERIA\n"
    "1. Organizational Capabilities  Points Based  25 (23.8% of Total)\n"
    "2. Marketing and Advertising    Points Based  20 (19.0% of Total)\n"
    "3. Media Planning and Buying    Points Based  10 (9.5% of Total)\n"
    "4. Social Media Support         Points Based  5 (4.8% of Total)\n"
)


def _rfp_with_table_beyond(cut: int) -> str:
    filler = ("General contract boilerplate and definitions. " * 2000)[:cut + 600]
    return filler + _TABLE


def test_scoring_table_past_the_old_40k_prefix_is_still_included():
    text = _rfp_with_table_beyond(40_000)
    assert "Organizational Capabilities" not in text[:40_000], "fixture must reproduce the bug"
    excerpt = _evaluation_excerpt(text)
    for row in (
        "Organizational Capabilities",
        "Marketing and Advertising",
        "Media Planning and Buying",
        "Social Media Support",
    ):
        assert row in excerpt, f"{row} missing from the evaluation excerpt"
    assert "Points Based" in excerpt
    assert "of Total" in excerpt


def test_falls_back_to_a_prefix_when_no_evaluation_region_is_detected():
    plain = "This document contains no scoring language whatsoever. " * 50
    excerpt = _evaluation_excerpt(plain)
    assert excerpt, "must never hand the agent an empty excerpt"


def test_short_rfp_is_unaffected():
    text = "Evaluation Criteria\n1. Price  Points Based  40\n2. Quality  Points Based  60\n"
    excerpt = _evaluation_excerpt(text)
    assert "Price" in excerpt and "Quality" in excerpt
