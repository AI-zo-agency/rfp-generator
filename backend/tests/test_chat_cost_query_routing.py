"""Chat assistant: skip redundant LLM hops without losing query understanding."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_chat_manuscript_fix import _deterministic_intent_classify
from app.services.proposal_evidence_gate import EvidenceDecision, EvidenceGateResult
from app.services.proposal_section_editor import (
    _advisory_needs_kb_lookup,
    _chat_improve_skip_kb,
    _section_chat_advisory_reply,
    _wants_section_edit,
)


def _section() -> ProposalSection:
    return ProposalSection(
        id="s1",
        title="Financial Stability",
        content="**Years in Operation:** 13 years",
        required=True,
        custom=False,
    )


class DeterministicIntentTests(unittest.TestCase):
    def test_question_without_question_mark_is_advisory(self) -> None:
        hit = _deterministic_intent_classify("what this section about")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["intent"], "advisory")

    def test_verify_ask_is_advisory(self) -> None:
        hit = _deterministic_intent_classify(
            "cross verify this is fabricated"
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["intent"], "advisory")

    def test_rewrite_command_is_single_edit(self) -> None:
        hit = _deterministic_intent_classify("rewrite this section to be concise")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["intent"], "single_edit")

    def test_whats_missing_is_not_an_edit(self) -> None:
        self.assertFalse(_wants_section_edit("what's missing from this section?"))
        self.assertFalse(_wants_section_edit("what is missing here?"))

    def test_mixed_check_then_fix_falls_through_to_llm(self) -> None:
        self.assertIsNone(
            _deterministic_intent_classify(
                "check the table and then rewrite it if anything is off"
            )
        )


class AdvisoryKbLookupTests(unittest.TestCase):
    def test_meaning_question_skips_kb(self) -> None:
        self.assertFalse(
            _advisory_needs_kb_lookup("what does this mean?", "Years in Operation")
        )

    def test_section_about_skips_kb(self) -> None:
        self.assertFalse(_advisory_needs_kb_lookup("what is this section about?", ""))

    def test_verify_ask_needs_kb(self) -> None:
        self.assertTrue(
            _advisory_needs_kb_lookup(
                "can you verify if it is Z'Onion?",
                "**Legal Name:** Z'Onion Creative Group LLC",
            )
        )

    def test_pinned_wrong_needs_kb(self) -> None:
        self.assertTrue(
            _advisory_needs_kb_lookup("here its wrong", "Email | info@zo.agency")
        )

    def test_is_this_correct_with_excerpt_needs_kb(self) -> None:
        self.assertTrue(
            _advisory_needs_kb_lookup("is this correct?", "**Legal Name:** Z'Onion")
        )


class SkipImprovePlannerTests(unittest.TestCase):
    def test_write_from_plan_skips_second_understand_llm(self) -> None:
        gate = EvidenceGateResult(
            action=EvidenceDecision.WRITE_FROM_PLAN,
            requires_retrieval=False,
            reason="style",
        )
        self.assertTrue(_chat_improve_skip_kb(gate, "make this shorter"))

    def test_retrieve_gate_keeps_planner(self) -> None:
        gate = EvidenceGateResult(
            action=EvidenceDecision.RETRIEVE_THEN_WRITE,
            requires_retrieval=True,
            reason="facts",
        )
        self.assertFalse(_chat_improve_skip_kb(gate, "add the legal name from the KB"))

    def test_none_gate_keeps_planner(self) -> None:
        self.assertFalse(_chat_improve_skip_kb(None, "rewrite this"))


class AdvisoryDoesNotPlanKbForMeaningAskTests(unittest.IsolatedAsyncioTestCase):
    async def test_what_is_this_section_does_not_call_query_planner(self) -> None:
        planned = AsyncMock(side_effect=AssertionError("planner must not run"))
        rfp = RfpRecord(
            id="r1",
            title="RFP",
            client="Calvert",
            sector="Government",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="t",
        )
        draft = ProposalDraft(
            rfpId="r1",
            sections=[_section()],
            updatedAt="2026-01-01T00:00:00Z",
        )
        with (
            patch(
                "app.services.proposal_section_editor._plan_verification_kb_queries",
                new=planned,
            ),
            patch(
                "app.services.proposal_section_editor.chat_json_with_repair",
                new=AsyncMock(return_value=({"reply": "This tab covers financial capacity."}, "stub")),
            ),
        ):
            reply, _ = await _section_chat_advisory_reply(
                section=_section(),
                rfp=rfp,
                rfp_context="A" * 9000,
                user_message="what is this section about",
                conversation_history=[],
                selection_text="",
                requirements_block="",
                manuscript_digest="1. Financial Stability",
                draft=draft,
            )
        planned.assert_not_awaited()
        self.assertIn("financial", reply.casefold())


if __name__ == "__main__":
    unittest.main()
