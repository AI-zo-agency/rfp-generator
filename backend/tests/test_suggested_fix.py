"""Suggested fix payload on advisory chat turns (Apply the fix UX)."""

from __future__ import annotations

from app.services.proposal_suggested_fix import (
    SuggestedFix,
    parse_advisory_suggested_fix,
    resolve_advisory_suggested_fix,
    validate_suggested_fix_section,
)
from app.models.proposal import ProposalDraft, ProposalSection


def test_parse_advisory_suggested_fix_when_has_fix():
    raw = {
        "reply": "The fee is wrong.",
        "hasFix": True,
        "applyInstruction": "In Budget, replace $5,000 with $4,200 from the Pricing Guide.",
        "summary": "Correct the monthly fee to guide rate",
        "sectionId": "sec-budget",
        "sectionTitle": "Budget",
    }
    fix = parse_advisory_suggested_fix(raw, fallback_section_id="sec-open")
    assert fix is not None
    assert fix.section_id == "sec-budget"
    assert "Pricing Guide" in fix.instruction
    assert fix.summary == "Correct the monthly fee to guide rate"


def test_parse_advisory_suggested_fix_absent_when_no_fix():
    assert (
        parse_advisory_suggested_fix(
            {"reply": "Looks fine.", "hasFix": False},
            fallback_section_id="sec-open",
        )
        is None
    )


def test_parse_advisory_suggested_fix_requires_instruction():
    assert (
        parse_advisory_suggested_fix(
            {"reply": "x", "hasFix": True, "applyInstruction": "  "},
            fallback_section_id="sec-open",
        )
        is None
    )


def test_parse_falls_back_to_open_section_id():
    fix = parse_advisory_suggested_fix(
        {
            "reply": "x",
            "hasFix": True,
            "applyInstruction": "Fix the typo in the first paragraph.",
        },
        fallback_section_id="sec-open",
    )
    assert fix is not None
    assert fix.section_id == "sec-open"


def test_validate_suggested_fix_section_rejects_unknown_id():
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-10T00:00:00Z",
        sections=[
            ProposalSection(id="sec-a", title="A", content="hi"),
        ],
    )
    fix = SuggestedFix(
        section_id="missing",
        instruction="Do a thing",
        summary="Do a thing",
    )
    assert validate_suggested_fix_section(fix, draft) is None


def test_validate_suggested_fix_section_accepts_known_id():
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-10T00:00:00Z",
        sections=[
            ProposalSection(id="sec-a", title="A", content="hi"),
        ],
    )
    fix = SuggestedFix(
        section_id="sec-a",
        instruction="Do a thing",
        summary="Do a thing",
    )
    validated = validate_suggested_fix_section(fix, draft)
    assert validated is not None
    assert validated.section_id == "sec-a"
    assert validated.section_title == "A"
    assert validated.instruction == "Do a thing"


_CLIENT_REFS_AUDIT = """
**Cannot confirm** — the draft contains multiple factual errors and unverifiable claims.

**City of Medford** contact information:
**Incorrect:** The draft lists Rich Rosenthal with phone (541) 774-2400.
**Problem:** The KB does not provide contact name, title, phone, email, or address.

**Deschutes Public Library:**
**KB coverage:** None.
**Problem:** Cannot confirm this client exists in the approved ClientList.

**Recommendation:**
- Remove or verify all contact details for City of Medford.
- Verify Deschutes Public Library against ClientList — if not approved, remove them.
"""


def test_resolve_fallback_when_model_omits_has_fix_but_lists_recommendations():
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-10T00:00:00Z",
        sections=[
            ProposalSection(
                id="sec-refs",
                title="Client References",
                content="City of Medford…",
            ),
        ],
    )
    fix = resolve_advisory_suggested_fix(
        {"reply": _CLIENT_REFS_AUDIT, "hasFix": False},
        fallback_section_id="sec-refs",
        section_title="Client References",
        draft=draft,
    )
    assert fix is not None
    assert fix.section_id == "sec-refs"
    assert "Client References" in fix.instruction
    assert "invent" in fix.instruction.casefold() or "KB" in fix.instruction
    assert "remove" in fix.instruction.casefold()


def test_resolve_no_fallback_for_clean_pass():
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-10T00:00:00Z",
        sections=[
            ProposalSection(id="sec-a", title="A", content="ok"),
        ],
    )
    assert (
        resolve_advisory_suggested_fix(
            {"reply": "**Correct** — this matches 01_companyfacts.", "hasFix": False},
            fallback_section_id="sec-a",
            section_title="A",
            draft=draft,
        )
        is None
    )


def test_resolve_prefers_model_instruction_when_present():
    draft = ProposalDraft(
        rfpId="r1",
        updatedAt="2026-08-10T00:00:00Z",
        sections=[
            ProposalSection(id="sec-a", title="A", content="ok"),
        ],
    )
    fix = resolve_advisory_suggested_fix(
        {
            "reply": _CLIENT_REFS_AUDIT,
            "hasFix": True,
            "applyInstruction": "In A, delete the Rich Rosenthal phone line only.",
            "summary": "Remove invented phone",
            "sectionId": "sec-a",
        },
        fallback_section_id="sec-a",
        section_title="A",
        draft=draft,
    )
    assert fix is not None
    assert "Rich Rosenthal phone" in fix.instruction
