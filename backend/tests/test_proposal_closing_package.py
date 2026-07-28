"""Compulsory closing + attachment pattern detection."""

from app.services.proposal_closing_package import detect_closing_components
from app.services.proposal_rfp_submission_requirements import (
    list_submission_checklist_from_rfp,
)


def test_closing_always_includes_commitment_even_without_match():
    text = (
        "Kennebec Valley Community College seeks a marketing plan. "
        "Submit technical ability, past performance, and cost."
    )
    comps = detect_closing_components(text)
    ids = {c.id for c in comps}
    assert "offeror_commitment" in ids
    commitment = next(c for c in comps if c.id == "offeror_commitment")
    assert commitment.section_id == "rfp-closing-commitment"
    assert "compulsory" in commitment.match_hint.casefold() or commitment.match_hint


def test_closing_detects_attachments_and_forms():
    text = """
    Required submission documents:
    - Acknowledgement of Addenda
    - Certificate of Insurance with Additional Insured
    - Form W-9
    - Exhibit A pricing schedule
    - Authorized signature of officer who can legally bind the firm
    Proposer must acknowledge all addenda.
    """
    comps = detect_closing_components(text)
    ids = {c.id for c in comps}
    assert "offeror_commitment" in ids
    assert "addenda_acknowledgement" in ids
    assert "insurance_attachments" in ids
    assert "authorized_signature" in ids


def test_empty_rfp_still_gets_compulsory_close():
    comps = detect_closing_components("")
    assert len(comps) == 1
    assert comps[0].id == "offeror_commitment"


def test_checklist_always_lists_closing():
    checklist = list_submission_checklist_from_rfp("Just a short RFP about marketing.")
    assert "Offeror commitment / closing statement" in checklist
