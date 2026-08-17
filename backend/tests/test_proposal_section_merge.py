"""Structural section merge for mislabeled duplicate tabs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_chat_ops import classify_chat_op
from app.services.proposal_section_merge import (
    apply_section_merge,
    plan_section_merge,
    user_asks_structural_section_merge,
)


class SectionMergeTests(unittest.TestCase):
    def _forms_body(self, *, portal: bool = True) -> str:
        intro = (
            "We confirm submission through the Dane County eProcurement Portal.\n\n"
            if portal
            else ""
        )
        return (
            "# Required Forms & Attachments\n\n"
            f"{intro}"
            "## Submission Compliance\n\n"
            "| **Required Item** | **Status** | **Notes** |\n"
            "| **References** | Included | contact details pending |\n"
        )

    def test_user_asks_structural_merge(self) -> None:
        msg = (
            "Section 12 References submission is mislabeled duplicate of Section 15. "
            "Keep Section 12 content, move under Section 15 title, delete Section 12."
        )
        self.assertTrue(user_asks_structural_section_merge(msg))
        self.assertEqual(classify_chat_op(msg), "none")

    def test_merge_forms_duplicate_tabs(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="ledger-comp-13",
                    title="References submission",
                    content=self._forms_body(portal=True),
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-req-forms-attachments",
                    title="Required Forms & Attachments",
                    content=self._forms_body(portal=False),
                    status="generated",
                ),
                ProposalSection(
                    id="other",
                    title="Cover",
                    content="Cover letter",
                    status="generated",
                ),
            ],
        )
        plan = plan_section_merge(
            draft,
            "Keep References submission content under Required Forms title; delete Section 12 duplicate.",
            open_section_id="ledger-comp-13",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.content_section_id, "ledger-comp-13")
        self.assertEqual(plan.title_section_id, "rfp-req-forms-attachments")
        self.assertEqual(plan.drop_section_id, "ledger-comp-13")

        updated, focus, logs = apply_section_merge(draft, plan)
        ids = [s.id for s in updated.sections]
        self.assertNotIn("ledger-comp-13", ids)
        self.assertIn("rfp-req-forms-attachments", ids)
        self.assertIn("eProcurement Portal", focus.content or "")
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
