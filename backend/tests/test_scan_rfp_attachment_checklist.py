"""Task 15 (scan-report half): the scan never told the user when the RFP
demands a PHYSICAL DOCUMENT be attached.

Root defect: list_submission_checklist_from_rfp
(proposal_rfp_submission_requirements.py) has 18 patterns for addenda
acknowledgements, affirmative-action questionnaires, W-9s, insurance
certificates, non-collusion affidavits and the like — but neither
proposal_verify_optional_scrub.py (the REAL Scan-RFP button path, mode=
"verify_scrub_only") nor proposal_rfp_compliance.py ever called it. The scan
relied entirely on whatever the compliance matrix happened to capture.

Worse, even where the pipeline DID act on a missing attachment (mode="full"
only, never reached by the button), it drafted a stub SECTION about the
item — "This RFP requires a W-9 as a submission deliverable... [MANUAL
FILL: attach ...]" — which is not the same thing as the physical document
being attached. A drafted section describing a W-9 is not a W-9.

Fix: list_submission_checklist_items_from_rfp classifies each pattern as
"narrative" (the pipeline can draft this) or "attachment" (a signed/scanned
physical document only a human can supply — never satisfiable by prose).
outstanding_submission_checklist_for_scan wires the checklist into the scan
path and cross-checks the current draft so an attachment already resolved
(a human replaced the [MANUAL FILL] stub with real confirmation text) is
not re-flagged on the next scan, while a still-open stub keeps being
flagged — because a stub about the document is not the document.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_rfp_submission_requirements import (
    list_submission_checklist_from_rfp,
    list_submission_checklist_items_from_rfp,
    outstanding_submission_checklist_for_scan,
)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(rfpId="rfp-1", sections=list(sections), updatedAt="2026-08-06T00:00:00Z")


class ChecklistClassificationTests(unittest.TestCase):
    """list_submission_checklist_from_rfp must keep returning plain labels
    (existing callers, e.g. proposal_presubmit_review.py, only need those);
    list_submission_checklist_items_from_rfp adds the kind split on top,
    same patterns, zero new LLM calls."""

    def test_w9_and_insurance_are_classified_as_attachment(self) -> None:
        text = "Vendor must submit a completed IRS Form W-9. Certificate of Insurance required."
        items = list_submission_checklist_items_from_rfp(text)
        by_label = {i.label: i for i in items}
        self.assertEqual(by_label["IRS Form W-9"].kind, "attachment")
        self.assertEqual(by_label["Certificate(s) of Insurance"].kind, "attachment")

    def test_financial_stability_and_awards_are_classified_as_narrative(self) -> None:
        text = "Describe the firm's financial stability. List awards and recognition received."
        items = list_submission_checklist_items_from_rfp(text)
        by_label = {i.label: i for i in items}
        self.assertEqual(by_label["Financial stability narrative (in proposal body)"].kind, "narrative")
        self.assertEqual(by_label["Awards & recognitions (in proposal body)"].kind, "narrative")

    def test_compulsory_close_is_always_present_and_narrative(self) -> None:
        items = list_submission_checklist_items_from_rfp("")
        by_label = {i.label: i for i in items}
        self.assertIn("Offeror commitment / closing statement", by_label)
        self.assertEqual(by_label["Offeror commitment / closing statement"].kind, "narrative")

    def test_backward_compatible_label_only_view_is_unchanged(self) -> None:
        text = "Submit a signed IRS Form W-9 and a Certificate of Insurance."
        labels = list_submission_checklist_from_rfp(text)
        self.assertIn("IRS Form W-9", labels)
        self.assertIn("Certificate(s) of Insurance", labels)
        self.assertIn("Offeror commitment / closing statement", labels)


class OutstandingChecklistSplitsCorrectlyTests(unittest.TestCase):
    """Required scenario: a scan over a proposal that needs (a) one
    narrative section drafted, (b) a signed W-9 attached, and (c) a
    certificate of insurance attached — all three must be visibly distinct,
    and the two attachment items must never be satisfiable by prose."""

    RFP_TEXT = (
        "Describe the firm's financial stability in the proposal narrative. "
        "Vendor must submit a completed IRS Form W-9. "
        "A Certificate of Insurance is required with the submission."
    )

    def test_empty_draft_reports_all_three_items_split_by_kind(self) -> None:
        draft = _draft(ProposalSection(id="s1", title="Approach", content="Our approach is sound."))
        result = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)

        self.assertIn("Financial stability narrative (in proposal body)", result.needs_drafting)
        self.assertIn("Offeror commitment / closing statement", result.needs_drafting)
        self.assertIn("IRS Form W-9", result.needs_attachment)
        self.assertIn("Certificate(s) of Insurance", result.needs_attachment)

        # The two categories must never mix.
        self.assertNotIn("IRS Form W-9", result.needs_drafting)
        self.assertNotIn(
            "Financial stability narrative (in proposal body)", result.needs_attachment
        )

    def test_a_drafted_manual_fill_stub_does_not_satisfy_an_attachment(self) -> None:
        """Regression for the exact failure mode this fix exists to
        prevent: a [MANUAL FILL] stub SECTION about the W-9 is not the W-9
        — it must keep being flagged, not silently read as resolved."""
        draft = _draft(
            ProposalSection(
                id="w9",
                title="IRS Form W-9",
                content=(
                    "This RFP requires **IRS Form W-9** as a submission deliverable.\n\n"
                    "- Status: **[MANUAL FILL: attach signed/complete file on buyer template]**\n"
                ),
            )
        )
        result = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)
        self.assertIn(
            "IRS Form W-9",
            result.needs_attachment,
            "a drafted stub about the document is not the document",
        )

    def test_a_human_replacing_the_stub_with_real_confirmation_resolves_it(self) -> None:
        """Idempotence: once a human replaces the stub with real
        confirmation (no open MANUAL FILL / placeholder marker left), the
        item must not be re-flagged on the next scan."""
        draft = _draft(
            ProposalSection(
                id="w9",
                title="IRS Form W-9",
                content="Signed IRS Form W-9 is attached to this submission as Exhibit C.",
            )
        )
        result = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)
        self.assertNotIn("IRS Form W-9", result.needs_attachment)

    def test_a_narrative_item_already_drafted_is_not_re_flagged(self) -> None:
        draft = _draft(
            ProposalSection(
                id="fin",
                title="Financial Stability",
                content=(
                    "zo agency has maintained positive cash flow and financial stability "
                    "for over a decade, with audited financials available on request."
                ),
            )
        )
        result = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)
        self.assertNotIn(
            "Financial stability narrative (in proposal body)", result.needs_drafting
        )

    def test_second_scan_on_an_unchanged_draft_reports_the_same_outstanding_items(self) -> None:
        """Idempotence, restated: calling this twice on the same draft must
        not grow or shrink the outstanding lists — it is a pure read."""
        draft = _draft(ProposalSection(id="s1", title="Approach", content="Our approach is sound."))
        first = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)
        second = outstanding_submission_checklist_for_scan(self.RFP_TEXT, draft)
        self.assertEqual(first.needs_drafting, second.needs_drafting)
        self.assertEqual(first.needs_attachment, second.needs_attachment)

    def test_an_rfp_with_no_submission_demands_still_reports_the_compulsory_close(self) -> None:
        draft = _draft(ProposalSection(id="s1", title="Approach", content="Our approach is sound."))
        result = outstanding_submission_checklist_for_scan("", draft)
        self.assertIn("Offeror commitment / closing statement", result.needs_drafting)
        self.assertEqual(result.needs_attachment, [])


if __name__ == "__main__":
    unittest.main()
