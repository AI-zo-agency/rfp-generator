"""Tests for agentic missing-answer fill."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_hollow_kb_fill import (
    fill_missing_answers_from_won_proposals,
    inventory_missing_answers,
    list_sections_needing_answer_fill,
    section_answers_missing,
)


class MissingAnswerDetectionTests(unittest.TestCase):
    def test_empty_qualification_bullets_are_missing(self) -> None:
        hollow = (
            "## Project Team\n\n"
            "**Lead**\n"
            "- Role: oversight\n"
            "- Qualifications:\n"
            "- Relevant projects:\n"
        )
        self.assertTrue(section_answers_missing(hollow))

    def test_manual_fill_without_verify_is_missing(self) -> None:
        body = (
            "## Drug-Free Workplace\n\n"
            "[MANUAL FILL: Draft full Drug-Free Workplace for this RFP.]"
        )
        self.assertTrue(section_answers_missing(body))

    def test_full_team_section_is_not_missing(self) -> None:
        full = (
            "## Project Team\n\n"
            "**Sonja Anderson**\n"
            "- Role: Agency Director\n"
            "- Qualifications: 20+ years municipal brand leadership\n"
            "- Relevant projects: City of Medford Rogue X\n"
        )
        self.assertFalse(section_answers_missing(full))

    def test_inventory_covers_whole_proposal_not_just_team(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-h",
            updatedAt="2026-08-21T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content="- Qualifications:\n",
                ),
                ProposalSection(
                    id="rfp-team",
                    title="Project Team",
                    content="**Lead**\n- Qualifications:\n- Relevant work:\n",
                ),
                ProposalSection(
                    id="rfp-closing-drug",
                    title="Drug-Free Workplace Program",
                    content="## Drug-Free\n\n[MANUAL FILL: need policy acknowledgment]",
                ),
                ProposalSection(
                    id="rfp-approach",
                    title="Approach",
                    content="## Approach\n\nFull methodology with phases and City review.",
                ),
            ],
        )
        gaps = inventory_missing_answers(draft)
        ids = {g.section_id for g in gaps}
        self.assertIn("rfp-team", ids)
        self.assertIn("rfp-closing-drug", ids)
        self.assertNotIn("section-1-who-we-are", ids)
        self.assertNotIn("rfp-approach", ids)
        targets = list_sections_needing_answer_fill(draft)
        self.assertEqual({s.id for s in targets}, ids)


class MissingAnswersAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_retrieve_fill_only_planned_sections(self) -> None:
        hollow = (
            "## Project Team\n\n"
            "**Lead**\n"
            "- Role: oversight\n"
            "- Qualifications:\n"
            "- Relevant projects:\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-h",
            updatedAt="2026-08-21T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-2-bio-sonja-anderson",
                    title="2.1 — Sonja Anderson",
                    content=(
                        "### Sonja Anderson\n"
                        "**Role on this engagement:** Agency Director\n"
                    ),
                ),
                ProposalSection(
                    id="rfp-team",
                    title="Project Team",
                    content=hollow,
                ),
                ProposalSection(
                    id="rfp-approach",
                    title="Approach",
                    content="## Approach\n\nFull methodology with phases and City review gates.",
                ),
            ],
        )
        filled = (
            "## Project Team\n\n"
            "**Sonja Anderson**\n"
            "- Role: Agency Director\n"
            "- Qualifications: Led municipal brand programs for comparable cities\n"
            "- Relevant projects: Medford Rogue X (from 06_WON)\n"
        )
        with patch(
            "app.services.proposal_hollow_kb_fill._plan_fills",
            new=AsyncMock(
                return_value=[
                    {
                        "sectionId": "rfp-team",
                        "kbQuery": "06_WON key personnel",
                        "gaps": ["empty fields: Qualifications"],
                    }
                ]
            ),
        ), patch(
            "app.services.proposal_hollow_kb_fill._retrieve_queries",
            new=AsyncMock(
                return_value=(
                    "### KB\n06_WON Medford — Sonja led brand strategy.",
                    ["06_WON_Medford.pdf"],
                )
            ),
        ), patch(
            "app.services.proposal_hollow_kb_fill._llm_fill_section",
            new=AsyncMock(return_value=filled),
        ):
            updated, logs = await fill_missing_answers_from_won_proposals(
                draft,
                rfp_title="Brand Development",
                rfp_client="North Miami Beach",
                rfp_id="rfp-h",
            )
        self.assertTrue(any("inventory" in line.lower() for line in logs))
        self.assertIn("Medford Rogue X", updated.sections[1].content or "")
        self.assertEqual(
            updated.sections[2].content,
            "## Approach\n\nFull methodology with phases and City review gates.",
        )


if __name__ == "__main__":
    unittest.main()
