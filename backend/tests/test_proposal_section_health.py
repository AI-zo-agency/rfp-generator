"""Section-health classification, driven by the shared fixture.

The same fixture is asserted by frontend/src/lib/proposal-section-health.test.ts.
If the two implementations ever drift, both suites fail.
"""

import json
from pathlib import Path

import pytest

from app.services.proposal_draft_llm import SECTION_DRAFT_FAILURE_PLACEHOLDER
from app.services.proposal_section_health import (
    SectionHealth,
    classify_section_health,
    is_dead_section,
    is_section_drafted,
)

FIXTURE = Path(__file__).parent / "fixtures" / "section_health_cases.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_classify_matches_shared_fixture(case):
    expected = SectionHealth(case["expected"]) if case["expected"] else None
    assert classify_section_health(case["content"]) == expected


def test_canonical_constant_is_detected():
    """The writer's constant must always be readable by the predicate."""
    assert classify_section_health(SECTION_DRAFT_FAILURE_PLACEHOLDER) is SectionHealth.DRAFT_FAILED


def test_production_comma_variant_is_detected():
    """The exact string stored for San Benito sections 9, 15 and 16.

    This is the bug: it differs from the canonical constant by one character, and
    exact-equality matching skipped it, so chat refused instead of regenerating.
    """
    stored = "[VERIFY: Section drafting failed, needs manual regeneration]"
    assert stored != SECTION_DRAFT_FAILURE_PLACEHOLDER.strip()
    assert is_dead_section(stored)


def test_inline_verify_chip_does_not_mark_section_dead():
    """The rule that protects finished work from being overwritten."""
    drafted = (
        "We accept the terms of the exemplar agreement without exception. "
        "[VERIFY: authorized signatory] The signed page is returned with our submission."
    )
    assert classify_section_health(drafted) is None
    assert is_section_drafted(drafted)


def test_none_and_empty_are_dead():
    assert classify_section_health(None) is SectionHealth.EMPTY
    assert is_dead_section("")
    assert not is_section_drafted("   ")


def test_drafted_and_dead_are_exact_complements():
    for case in CASES:
        content = case["content"]
        assert is_section_drafted(content) is not is_dead_section(content)
