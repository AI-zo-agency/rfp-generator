"""Forms/attachments must not mint one sidebar tab per Bid Form row."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_submission_requirements import (
    SubmissionDeliverable,
    _FORMS_ATTACHMENTS_SECTION_ID,
    _FORMS_ATTACHMENTS_TITLE,
    detect_missing_submission_deliverables,
    ensure_all_rfp_submission_requirements,
)


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp-1",
        title="Providence Design RFP",
        client="Providence",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
    )


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=list(sections),
        updatedAt="2026-08-06T00:00:00Z",
    )


class DetectMissingFormsStayFormishTests(unittest.TestCase):
    def test_forms_are_returned_but_not_as_must_in_manuscript_only(self) -> None:
        inventory = [
            SubmissionDeliverable(
                id="f1",
                title="Bid Form A",
                section_id="rfp-req-bid-form-a",
                kind="signed_form",
                must_in_manuscript=False,
                draft_instructions="Sign buyer form",
            ),
            SubmissionDeliverable(
                id="n1",
                title="Cybersecurity Incident Response Plan",
                section_id="rfp-req-cyber-irp",
                kind="narrative_proposal",
                must_in_manuscript=True,
                draft_instructions="Write IRP narrative",
            ),
        ]
        draft = _draft(
            ProposalSection(
                id="s1", title="Approach", content="Our approach is thorough."
            )
        )
        missing = detect_missing_submission_deliverables(draft, inventory)
        kinds = {m.kind for m in missing}
        self.assertIn("signed_form", kinds)
        self.assertIn("narrative_proposal", kinds)


class EnsureSubmissionConsolidatesFormsTests(unittest.IsolatedAsyncioTestCase):
    async def test_eight_bid_forms_become_one_tab_plus_checklist(self) -> None:
        inventory = [
            SubmissionDeliverable(
                id=f"f{i}",
                title=f"Bid Form {i}",
                section_id=f"rfp-req-bid-form-{i}",
                kind="signed_form",
                must_in_manuscript=False,
                draft_instructions="Sign",
            )
            for i in range(8)
        ]
        draft = _draft(
            ProposalSection(id="s1", title="Approach", content="Real content " * 20)
        )

        with (
            patch(
                "app.services.proposal_rfp_submission_requirements.inventory_rfp_submission_requirements",
                new=AsyncMock(return_value=inventory),
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.submission_documents_excerpt",
                return_value="Submit Bid Forms 1-8.",
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.list_submission_checklist_from_rfp",
                return_value=[],
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.detect_narrative_submission_gaps",
                return_value=[],
            ),
        ):
            updated, added, logs, checklist = await ensure_all_rfp_submission_requirements(
                draft=draft,
                rfp=_rfp(),
                rfp_text="Submit Bid Forms 1-8 signed.",
                research=None,
            )

        titles = [s.title for s in updated.sections]
        self.assertEqual(titles.count(_FORMS_ATTACHMENTS_TITLE), 1)
        self.assertFalse(any(t.startswith("Bid Form") for t in titles))
        self.assertEqual(len(updated.sections), 2)  # approach + consolidated
        self.assertEqual(added[0].section_id, _FORMS_ATTACHMENTS_SECTION_ID)
        self.assertTrue(any("not one section per form" in line for line in logs))
        self.assertGreaterEqual(len(checklist), 8)

    async def test_legacy_per_form_stubs_are_collapsed(self) -> None:
        legacy = [
            ProposalSection(
                id=f"rfp-req-bid-form-{i}",
                title=f"Bid Form {i}",
                content=f"## Bid Form {i}\n\n[MANUAL FILL: attach signed form]",
            )
            for i in range(4)
        ]
        draft = _draft(
            ProposalSection(id="s1", title="Approach", content="Real content " * 20),
            *legacy,
        )
        with (
            patch(
                "app.services.proposal_rfp_submission_requirements.inventory_rfp_submission_requirements",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.submission_documents_excerpt",
                return_value="",
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.list_submission_checklist_from_rfp",
                return_value=[],
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.detect_narrative_submission_gaps",
                return_value=[],
            ),
        ):
            updated, _added, logs, _checklist = await ensure_all_rfp_submission_requirements(
                draft=draft,
                rfp=_rfp(),
                rfp_text="x",
                research=None,
            )

        self.assertEqual(len(updated.sections), 1)
        self.assertTrue(any("collapsed" in line for line in logs))

    async def test_long_form_stubs_are_still_collapsed(self) -> None:
        """LLM-expanded Bid Form tabs (2k+ chars) must not block collapse."""
        legacy = [
            ProposalSection(
                id=f"rfp-req-bid-form-{i}",
                title=f"Bid Form {i}",
                content=(
                    f"## Bid Form {i}\n\n"
                    + "\n".join(
                        f"- [MANUAL FILL: field {j}]" for j in range(12)
                    )
                ),
            )
            for i in range(3)
        ]
        draft = _draft(
            ProposalSection(id="s1", title="Approach", content="Real content " * 20),
            *legacy,
        )
        with (
            patch(
                "app.services.proposal_rfp_submission_requirements.inventory_rfp_submission_requirements",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.submission_documents_excerpt",
                return_value="",
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.list_submission_checklist_from_rfp",
                return_value=[],
            ),
            patch(
                "app.services.proposal_rfp_submission_requirements.detect_narrative_submission_gaps",
                return_value=[],
            ),
        ):
            updated, _added, logs, _checklist = await ensure_all_rfp_submission_requirements(
                draft=draft,
                rfp=_rfp(),
                rfp_text="x",
                research=None,
            )

        self.assertEqual(len(updated.sections), 1)
        self.assertTrue(any("collapsed" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
