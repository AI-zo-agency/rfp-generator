"""Compulsory closing + attachment pattern detection."""

from app.services.proposal_closing_package import detect_closing_components
from app.services.proposal_rfp_submission_requirements import (
    list_submission_checklist_from_rfp,
)


def test_commitment_not_added_unless_caller_opts_in():
    """Deliberate change: the closing statement is no longer unconditional.

    An RFP that never asks for a closing statement should not get one. Under a
    page cap an unrequested section displaces content the RFP does require.
    Callers that genuinely want it can still pass always_include_commitment.
    """
    text = (
        "Kennebec Valley Community College seeks a marketing plan. "
        "Submit technical ability, past performance, and cost."
    )
    assert "offeror_commitment" not in {c.id for c in detect_closing_components(text)}

    comps = detect_closing_components(text, always_include_commitment=True)
    commitment = next(c for c in comps if c.id == "offeror_commitment")
    assert commitment.section_id == "rfp-closing-commitment"
    assert commitment.match_hint


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
    # Genuine submission requirements still detected under the obligation gate.
    assert "addenda_acknowledgement" in ids
    assert "insurance_attachments" in ids
    assert "authorized_signature" in ids


def test_empty_rfp_yields_nothing_by_default():
    """No RFP text means nothing is evidenced as required."""
    assert detect_closing_components("") == []

    comps = detect_closing_components("", always_include_commitment=True)
    assert [c.id for c in comps] == ["offeror_commitment"]


def test_checklist_always_lists_closing():
    checklist = list_submission_checklist_from_rfp("Just a short RFP about marketing.")
    assert "Offeror commitment / closing statement" in checklist
