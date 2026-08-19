"""Closing package adapters — ledger is authority (no regex catalog)."""

from __future__ import annotations

from app.services.proposal_closing_package import detect_closing_components
from app.services.proposal_closing_ledger import ledger_from_fixture
from app.services.proposal_rfp_submission_requirements import (
    list_submission_checklist_from_rfp,
)


def test_detect_without_ledger_returns_empty() -> None:
    text = (
        "Submit three references. Complete the Pricing Proposal Form "
        "(hourly, monthly, annual). Return Acknowledgement of Addenda. "
        "Attach Certificate of Insurance and W-9. Sign as authorized representative. "
        "Acknowledge the exemplar agreement with no exceptions."
    )
    assert detect_closing_components(text) == []


def test_detect_with_ledger_fixture() -> None:
    ledger = ledger_from_fixture(
        [
            {"id": "references", "title": "References", "kind": "form"},
            {"id": "pricing_form", "title": "Pricing Proposal Form", "kind": "form"},
            {
                "id": "offeror_commitment",
                "title": "Offeror Commitment & Closing Statement",
                "kind": "narrative",
            },
        ]
    )
    comps = detect_closing_components("ignored", ledger=ledger)
    ids = {c.id for c in comps}
    assert "references" in ids
    assert "pricing_form" in ids
    assert "offeror_commitment" in ids


def test_commitment_opt_in_without_ledger() -> None:
    comps = detect_closing_components("", always_include_commitment=True)
    assert {c.id for c in comps} == {"offeror_commitment"}


def test_empty_rfp_without_opt_in() -> None:
    assert detect_closing_components("") == []


def test_submission_checklist_still_lists_common_items() -> None:
    checklist = list_submission_checklist_from_rfp("Just a short RFP about marketing.")
    assert isinstance(checklist, list)
