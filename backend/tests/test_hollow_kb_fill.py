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
                        "kbQueries": ["06_WON key personnel", "06_WON Sonja Anderson"],
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
        ) as mock_retrieve, patch(
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
        # Both of _plan_fills's per-gap kbQueries reached _retrieve_queries,
        # flattened — not just the first one, and not dropped because the
        # planner returned the new plural-array shape.
        mock_retrieve.assert_awaited_once_with(
            ["06_WON key personnel", "06_WON Sonja Anderson"]
        )


class PlanFillsMultiQueryTests(unittest.IsolatedAsyncioTestCase):
    """_plan_fills must plan several targeted KB queries per gap, not one —
    this is the fix for "chat's Improve section works well, the automated
    fill doesn't": chat runs several targeted searches per ask, the
    automated pass was running exactly one generic one per section."""

    def _gap(self):
        from app.services.proposal_hollow_kb_fill import MissingAnswerGap

        return MissingAnswerGap(
            section_id="rfp-references",
            title="References",
            reasons=["empty reference list"],
            snippet="",
        )

    async def test_multiple_kb_queries_per_gap_are_parsed(self) -> None:
        from app.services.proposal_hollow_kb_fill import _plan_fills

        response = {
            "fills": [
                {
                    "sectionId": "rfp-references",
                    "kbQueries": [
                        "06_WON Maricopa County reference contact",
                        "06_WON Hillsboro Public Library reference contact",
                        "07_FIN references similar scope",
                    ],
                    "gaps": ["empty reference list"],
                }
            ]
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(response, "openrouter")),
        ):
            planned = await _plan_fills(
                [self._gap()],
                rfp_title="Test RFP",
                rfp_client="Test City",
                rfp_sector="government",
                known_case_study_clients=["Maricopa County", "Hillsboro Public Library"],
            )
        self.assertEqual(len(planned), 1)
        self.assertEqual(len(planned[0]["kbQueries"]), 3)
        self.assertIn("Maricopa County", planned[0]["kbQueries"][0])

    async def test_legacy_single_kb_query_shape_still_works(self) -> None:
        """Back-compat: if the model (or a cached/older path) returns the old
        singular "kbQuery" key, it must still normalize into a 1-item list,
        not silently vanish and leave that section with no search at all."""
        from app.services.proposal_hollow_kb_fill import _plan_fills

        response = {
            "fills": [
                {
                    "sectionId": "rfp-references",
                    "kbQuery": "06_WON references",
                    "gaps": ["empty reference list"],
                }
            ]
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(response, "openrouter")),
        ):
            planned = await _plan_fills(
                [self._gap()],
                rfp_title="Test RFP",
                rfp_client="Test City",
                rfp_sector="government",
            )
        self.assertEqual(planned[0]["kbQueries"], ["06_WON references"])

    async def test_known_case_study_clients_reach_the_planner_prompt(self) -> None:
        from app.services.proposal_hollow_kb_fill import _plan_fills

        captured: dict = {}

        async def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {"fills": []}, "openrouter"

        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json", new=fake_chat_json
        ):
            await _plan_fills(
                [self._gap()],
                rfp_title="Test RFP",
                rfp_client="Test City",
                rfp_sector="government",
                known_case_study_clients=["Maricopa County", "Hillsboro Public Library"],
            )
        user_content = captured["messages"][1]["content"]
        self.assertIn("Maricopa County", user_content)
        self.assertIn("Hillsboro Public Library", user_content)


class InstructionalChecklistRejectionTests(unittest.IsolatedAsyncioTestCase):
    """Regression: a fill that describes what a human should do instead of
    doing it must never ship — reject it like any other failed fill, leaving
    the section as-is, rather than saving a to-do list as if it were content."""

    async def test_checklist_shaped_fill_is_rejected(self) -> None:
        from app.services.proposal_hollow_kb_fill import _llm_fill_section

        section = ProposalSection(
            id="rfp-references",
            title="References (Three Satisfactory References)",
            content="[MANUAL FILL: Draft full References for this RFP.]",
        )
        checklist_response = {
            "content": (
                "## References (Three Satisfactory References)\n\n"
                "REFERENCE SUBMITTAL CHECKLIST\n\n"
                "[ ] Confirm whether the City has issued a standard reference "
                "form (some RFPs include a fillable reference contact sheet).\n\n"
                "[ ] For each reference, provide: client/organization name, "
                "contact name and title, phone number, email address, and a "
                "brief description of the services performed.\n\n"
                "[ ] Select three references similar in scope to this "
                "engagement, completed within the past three years.\n"
            )
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(checklist_response, "openrouter")),
        ):
            result = await _llm_fill_section(
                section=section,
                gaps=["missing references"],
                evidence="### KB\nsome evidence",
                draft_context="OUR WORK: Hillsboro Public Library",
                rfp_title="Test RFP",
                rfp_client="Test City",
            )
        self.assertIsNone(result)

    async def test_real_reference_content_with_inline_verify_tags_is_accepted(
        self,
    ) -> None:
        from app.services.proposal_hollow_kb_fill import _llm_fill_section

        section = ProposalSection(
            id="rfp-references",
            title="References (Three Satisfactory References)",
            content="[MANUAL FILL: Draft full References for this RFP.]",
        )
        real_response = {
            "content": (
                "## References (Three Satisfactory References)\n\n"
                "1. **Hillsboro Public Library** — Contact: [VERIFY: contact "
                "name], [VERIFY: phone], [VERIFY: email]. Social media "
                "management, 2023-2024.\n"
                "2. **Maricopa County** — Contact: [VERIFY: contact name], "
                "[VERIFY: phone], [VERIFY: email]. Public sector "
                "communications, 2022-present.\n"
                "3. **Hampton Lumber** — Contact: [VERIFY: contact name], "
                "[VERIFY: phone], [VERIFY: email]. Brand marketing, 2023.\n"
            )
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(real_response, "openrouter")),
        ):
            result = await _llm_fill_section(
                section=section,
                gaps=["missing references"],
                evidence="### KB\nsome evidence",
                draft_context="OUR WORK: Hillsboro Public Library",
                rfp_title="Test RFP",
                rfp_client="Test City",
            )
        self.assertIsNotNone(result)
        self.assertIn("Hillsboro Public Library", result)

    async def test_nested_bare_verify_tag_inside_manual_fill_is_sanitized(
        self,
    ) -> None:
        """Real incident repro: the model used "[VERIFY]" (no colon — not a
        real tag) as a word inside its own [MANUAL FILL: ...] note ("do not
        leave [VERIFY] shells"). The canonical MANUAL_FILL_TAG_RE (and every
        other tag regex in this codebase) matches up to the FIRST "]" it
        finds, so this nested bracket split the outer tag in two — corrupting
        both backend tag-scanning and the UI's chip rendering. The saved
        content must never contain that inner bare tag."""
        from app.services.proposal_hollow_kb_fill import _llm_fill_section

        section = ProposalSection(
            id="rfp-compulsory-gap-references",
            title="Qualifications and Experience of the Firm",
            content="[MANUAL FILL: Draft full References for this RFP.]",
        )
        raw_response = {
            "content": (
                "## Qualifications and Experience of the Firm\n\n"
                "**Reference 1 — Maricopa County**\n"
                "Contact: Anna Le, Procurement Officer\n"
                "Phone: [VERIFY: phone from KB reference doc]\n"
                "Email: anna.le@maricopa.gov\n\n"
                "[MANUAL FILL: Sonja — remaining references must come from "
                "verified ClientList / KB contacts only (name, title, org, "
                "phone, email). Do not invent or leave [VERIFY] shells.]"
            )
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(raw_response, "openrouter")),
        ):
            result = await _llm_fill_section(
                section=section,
                gaps=["missing references"],
                evidence="### KB\nsome evidence",
                draft_context="OUR WORK: Maricopa County",
                rfp_title="Test RFP",
                rfp_client="Test City",
            )
        self.assertIsNotNone(result)
        # The real, well-formed tag must survive untouched.
        self.assertIn("[VERIFY: phone from KB reference doc]", result)
        # The nested bare tag must be neutralized — no "[VERIFY]" left in the
        # saved content anywhere.
        self.assertNotIn("[VERIFY]", result)
        self.assertIn("leave VERIFY shells", result)

    async def test_rfp_demanded_submission_checklist_is_accepted(self) -> None:
        """An RFP can legitimately require a submission/compliance checklist
        as the actual deliverable — that must never be rejected just because
        it uses the same "[ ]" shape as the process-narration failure mode."""
        from app.services.proposal_hollow_kb_fill import _llm_fill_section

        section = ProposalSection(
            id="rfp-submittal-checklist",
            title="Submittal Checklist",
            content="[MANUAL FILL: Draft full Submittal Checklist for this RFP.]",
        )
        checklist_response = {
            "content": (
                "## Submittal Checklist\n\n"
                "[ ] Cover Letter — Included, see Tab 1\n\n"
                "[ ] Signed Addendum #1 Acknowledgment — Attached as Exhibit A\n\n"
                "[ ] W-9 Form — [VERIFY: confirm current copy is on file with "
                "accounting]\n\n"
                "[ ] Certificate of Insurance — [VERIFY: attach current "
                "certificate before submission]\n\n"
                "[ ] Statement of Qualifications — Included, see Tab 3\n"
            )
        }
        with patch(
            "app.services.llm.is_configured", return_value=True
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(return_value=(checklist_response, "openrouter")),
        ):
            result = await _llm_fill_section(
                section=section,
                gaps=["missing submittal checklist"],
                evidence="### KB\nsome evidence",
                draft_context="COMPANY: 1.1 — Who We Are",
                rfp_title="Test RFP",
                rfp_client="Test City",
            )
        self.assertIsNotNone(result)
        self.assertIn("Cover Letter", result)


if __name__ == "__main__":
    unittest.main()
