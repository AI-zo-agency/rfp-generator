"""Persist must run active brand-voice (Rev 7) scrub — if it is not zō, change it."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_zero_fabrication import (
    apply_zero_fabrication_guards_before_persist,
)

NOW = "2026-11-05T00:00:00Z"


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(
                id="approach",
                title="Approach",
                content="The Vendor delivers a robust seamless solution.",
                status="generated",
                mode="write",
            ),
            ProposalSection(
                id="cost",
                title="Cost and Price Proposal",
                content="| Task | Investment |\n|---|---:|\n| A1 | $3,000 |\n",
                status="generated",
                mode="write",
            ),
        ],
        updatedAt=NOW,
    )


class BeforePersistRev7VoiceGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_persist_runs_active_voice_scrub(self) -> None:
        draft = _draft()
        voiced = draft.model_copy(
            update={
                "sections": [
                    draft.sections[0].model_copy(
                        update={"content": "We deliver the work."}
                    ),
                    draft.sections[1],
                ]
            }
        )
        with (
            patch(
                "app.services.proposal_zero_fabrication.apply_zero_fabrication_guards",
                return_value=(draft, type("R", (), {"logs": []})()),
            ),
            patch(
                "app.services.proposal_voice_enforcement.apply_rev6_voice_scrub_to_draft",
                new_callable=AsyncMock,
                return_value=(voiced, ["approach: voice repaired"]),
            ) as voice,
            patch(
                "app.services.proposal_forms_attachments_integrity.audit_and_repair_forms_attachments",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.proposal_agentic_qc_repair.run_agentic_manuscript_qc_repair",
                new_callable=AsyncMock,
                return_value=(voiced, []),
            ),
            patch(
                "app.services.proposal_capability_bio_grounding.ground_bios_to_kb",
                new_callable=AsyncMock,
                return_value=(voiced, []),
            ),
            patch(
                "app.services.evidence_trust.personnel_grounding.scrub_unverified_personnel_from_draft",
                new_callable=AsyncMock,
                return_value=(voiced, []),
            ),
            patch(
                "app.services.proposal_citation_grounding.attach_citation_maps_to_draft",
                return_value=(voiced, []),
            ),
        ):
            out, report = await apply_zero_fabrication_guards_before_persist(draft)

        voice.assert_awaited()
        kwargs = voice.await_args.kwargs
        self.assertIn("cost", kwargs.get("exclude_section_ids") or set())
        self.assertEqual(out.sections[0].content, "We deliver the work.")
        self.assertTrue(any("voice" in (line or "").casefold() for line in report.logs))

    async def test_confirm_preview_apply_skips_voice_llm(self) -> None:
        draft = _draft()
        with (
            patch(
                "app.services.proposal_zero_fabrication.apply_zero_fabrication_guards",
                return_value=(draft, type("R", (), {"logs": []})()),
            ),
            patch(
                "app.services.proposal_voice_enforcement.apply_rev6_voice_scrub_to_draft",
                new_callable=AsyncMock,
            ) as voice,
            patch(
                "app.services.proposal_capability_bio_grounding.ground_bios_to_kb",
                new_callable=AsyncMock,
                return_value=(draft, []),
            ),
            patch(
                "app.services.evidence_trust.personnel_grounding.scrub_unverified_personnel_from_draft",
                new_callable=AsyncMock,
                return_value=(draft, []),
            ),
            patch(
                "app.services.proposal_citation_grounding.attach_citation_maps_to_draft",
                return_value=(draft, []),
            ),
        ):
            await apply_zero_fabrication_guards_before_persist(
                draft, skip_llm_repairs=True
            )

        voice.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
