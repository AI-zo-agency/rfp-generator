"""Gov-policy / disqualification hardening — fail closed before submit."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_presubmit_review import (
    _compliance_checklist,
    _scan_submission_document_gaps,
)
from app.services.proposal_rfp_submission_requirements import (
    list_submission_checklist_items_from_rfp,
    outstanding_submission_checklist_for_scan,
)
from app.services.proposal_scan_dq_orchestrator import collect_rfp_text_dq_risks


def _rfp(**kw) -> RfpRecord:
    base = dict(
        id="rfp-gov",
        title="County Marketing Services",
        client="Sample County",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="n",
    )
    base.update(kw)
    return RfpRecord(**base)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-gov",
        sections=list(sections),
        updatedAt="2026-08-05T00:00:00+00:00",
    )


class ExpandedAttachmentCatalogTests(unittest.TestCase):
    def test_everify_bond_and_sealed_package_are_attachments(self) -> None:
        text = (
            "Offeror must enroll in E-Verify and submit the affidavit. "
            "A performance bond is required. Submit in a sealed envelope with original signature."
        )
        items = list_submission_checklist_items_from_rfp(text)
        labels = {i.label: i.kind for i in items}
        self.assertEqual(labels.get("E-Verify affidavit / enrollment"), "attachment")
        self.assertEqual(labels.get("Bid / performance / payment bond"), "attachment")
        self.assertEqual(labels.get("Sealed package / original signature"), "attachment")


class UnresolvedAttachmentBlocksSubmitTests(unittest.TestCase):
    def test_manual_fill_w9_stub_is_critical_not_ready(self) -> None:
        rfp_text = "Vendor must submit a completed IRS Form W-9 with the proposal."
        draft = _draft(
            ProposalSection(
                id="w9",
                title="IRS Form W-9",
                content=(
                    "This RFP requires a W-9.\n\n"
                    "[MANUAL FILL: attach signed/complete W-9 file before export.]"
                ),
            )
        )
        issues = _scan_submission_document_gaps(
            draft=draft, rfp=_rfp(), rfp_text=rfp_text
        )
        self.assertTrue(
            any(i.severity == "critical" and "W-9" in i.message for i in issues),
            issues,
        )
        outstanding = outstanding_submission_checklist_for_scan(rfp_text, draft)
        self.assertIn("IRS Form W-9", outstanding.needs_attachment)

    def test_page_limit_uses_rfp_text_when_field_empty(self) -> None:
        rfp = _rfp(pageLimit=None)
        # Huge manuscript vs 5-page RFP limit in text
        fat = "word " * 2500
        draft = _draft(ProposalSection(id="a", title="Approach", content=fat))
        items = _compliance_checklist(
            draft=draft,
            research=None,
            rfp=rfp,
            rfp_text="The proposal is limited to five (5) pages.",
        )
        page_items = [i for i in items if "Page limit" in i.item]
        self.assertTrue(page_items)
        self.assertEqual(page_items[0].status, "fail")


class DqRiskCatalogTests(unittest.TestCase):
    def test_sealed_and_separate_cost_surface_as_dq_risks(self) -> None:
        rfp = _rfp()
        draft = _draft(ProposalSection(id="a", title="Tech", content="Approach text."))
        risks = collect_rfp_text_dq_risks(
            rfp=rfp,
            draft=draft,
            rfp_text=(
                "Technical and cost proposals must be submitted in separate sealed envelopes. "
                "Do not include pricing in the technical volume."
            ),
            ledger_result=None,
        )
        blob = " ".join(risks).casefold()
        self.assertTrue(
            "sealed" in blob or "separate" in blob or "pricing" in blob,
            risks,
        )


if __name__ == "__main__":
    unittest.main()
