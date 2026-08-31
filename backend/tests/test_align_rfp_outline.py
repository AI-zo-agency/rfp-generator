"""Align to RFP outline — detector + layout-only pass."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_align_rfp_outline import (
    build_align_preview,
    message_asks_align_rfp_outline,
    run_align_to_rfp_outline,
)


class AlignAskDetectorTests(unittest.TestCase):
    def test_whole_packet_asks(self) -> None:
        self.assertTrue(
            message_asks_align_rfp_outline(
                "The formatting needs to be completely rearranged per the RFP"
            )
        )
        self.assertTrue(message_asks_align_rfp_outline("put sections in RFP order"))
        self.assertTrue(message_asks_align_rfp_outline("Align to RFP outline"))
        self.assertTrue(
            message_asks_align_rfp_outline(
                "Match the proposal to the RFP submission format and order"
            )
        )

    def test_section_polish_is_not_align(self) -> None:
        self.assertFalse(message_asks_align_rfp_outline("make Who We Are warmer"))
        self.assertFalse(message_asks_align_rfp_outline("Improve this section"))
        self.assertFalse(message_asks_align_rfp_outline("fix the budget summary"))
        self.assertFalse(message_asks_align_rfp_outline("fix the tone of Who We Are"))


class AlignOutlineRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_calls_structure_pass_without_llm(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-align",
            sections=[
                ProposalSection(
                    id="who",
                    title="Who We Are",
                    content="Brand prose.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
                ProposalSection(
                    id="price",
                    title="Price",
                    content="Fees.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
            ],
            updatedAt="2026-08-27T00:00:00+00:00",
        )
        rfp = RfpRecord(
            id="rfp-align",
            title="Test",
            client="Client",
            sector="Edu",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="t",
        )

        with (
            patch(
                "app.services.proposal_align_rfp_outline.load_rfp_for_proposal",
                return_value=(rfp, "", "SECTION A\nSECTION B"),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.aget_research_cache",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.asave_proposal_draft",
                new=AsyncMock(),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.push_proposal_snapshot",
                side_effect=lambda d, label: d,
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
                new=AsyncMock(),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.run_rfp_structure_alignment_pass",
                new=AsyncMock(return_value=(draft, ["ordered"], [])),
            ) as align_pass,
        ):
            report = await run_align_to_rfp_outline("rfp-align")

        self.assertEqual(report["mode"], "align-rfp-outline")
        self.assertIn("summary", report)
        kwargs = align_pass.await_args.kwargs
        self.assertFalse(kwargs["use_llm"])
        self.assertTrue(kwargs["include_missing_submittals"])


class AlignOutlineStalePendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_ignores_stale_pending_preview_and_rescans(self) -> None:
        """If the draft changed after Align preview was computed (e.g. a
        gap-fill pass added a new section, or the user edited content), Apply
        must not blindly overwrite the current sections with the frozen
        preview snapshot — that would silently drop the new section and
        revert any edits made in between."""
        stale_proposed = ProposalDraft(
            rfpId="rfp-align",
            sections=[
                ProposalSection(
                    id="who",
                    title="Who We Are",
                    content="Old wording.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
                ProposalSection(
                    id="price",
                    title="Price",
                    content="Old fees.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
            ],
            updatedAt="2026-08-27T00:00:00+00:00",
        )
        current_draft = ProposalDraft(
            rfpId="rfp-align",
            sections=[
                ProposalSection(
                    id="who",
                    title="Who We Are",
                    content="New edited wording made after the preview.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
                ProposalSection(
                    id="price",
                    title="Price",
                    content="Fees.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
                ProposalSection(
                    id="scope",
                    title="Scope of Work",
                    content="Added by a gap-fill pass after the preview.",
                    source="rfp",
                    mode="write",
                    wordTarget=200,
                    status="generated",
                ),
            ],
            updatedAt="2026-08-28T00:00:00+00:00",
            pendingAlignRfpOutline={
                "preview": {"changes": ["Reorder the names on the left"]},
                "proposedDraft": stale_proposed.model_dump(by_alias=True, mode="json"),
                "createdAt": "2026-08-27T00:00:00+00:00",
                "basedOnUpdatedAt": "2026-08-27T00:00:00+00:00",
            },
        )
        rfp = RfpRecord(
            id="rfp-align",
            title="Test",
            client="Client",
            sector="Edu",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="t",
        )
        fresh_result = current_draft.model_copy(
            update={"pending_align_rfp_outline": None}
        )

        with (
            patch(
                "app.services.proposal_align_rfp_outline.load_rfp_for_proposal",
                return_value=(rfp, "", "SECTION A\nSECTION B"),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.aget_proposal_draft",
                new=AsyncMock(return_value=current_draft),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.aget_research_cache",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.asave_proposal_draft",
                new=AsyncMock(),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.push_proposal_snapshot",
                side_effect=lambda d, label: d,
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
                new=AsyncMock(),
            ),
            patch(
                "app.services.proposal_align_rfp_outline.run_rfp_structure_alignment_pass",
                new=AsyncMock(return_value=(fresh_result, ["reordered"], [])),
            ) as align_pass,
        ):
            report = await run_align_to_rfp_outline("rfp-align")

        align_pass.assert_awaited_once()
        self.assertFalse(report["usedPendingPreview"])
        self.assertIn("Scope of Work", report["afterTitles"])


class AlignPreviewBuildTests(unittest.TestCase):
    def test_build_align_preview_side_by_side(self) -> None:
        preview = build_align_preview(
            current_titles=["Who We Are", "Price"],
            proposed_titles=["Who We Are", "Approach", "Price"],
            rfp_needed_titles=["Who We Are", "Approach", "Price"],
        )
        self.assertFalse(preview["nothingToChange"])
        self.assertIn("Approach", preview["addedTitles"])
        self.assertTrue(any("Add empty slot" in c for c in preview["changes"]))
        self.assertEqual(preview["currentTitles"][0], "Who We Are")
        self.assertEqual(preview["proposedTitles"][1], "Approach")

    def test_build_align_preview_already_matches(self) -> None:
        preview = build_align_preview(
            current_titles=["A", "B"],
            proposed_titles=["A", "B"],
            rfp_needed_titles=["A", "B"],
        )
        self.assertTrue(preview["nothingToChange"])


if __name__ == "__main__":
    unittest.main()
