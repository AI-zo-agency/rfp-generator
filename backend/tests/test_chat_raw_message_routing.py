"""Intent / structure routing must see the user's words, not evidence scaffolding.

improve_proposal_section prepends an evidence-policy stanza onto `user_message`
for rewrite prompts. That stanza contains edit verbs ("replace", "remove") and
section-id noise. Classifying or planning on the augmented string makes the
assistant misunderstand ordinary asks.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_section_editor as editor


def _section() -> ProposalSection:
    return ProposalSection(
        id="section-20-budget",
        title="20. Budget & Pricing",
        content="## Proposed investment\n\nFees TBD.",
        source="template",
        mode="write",
    )


def _rfp():
    from app.models.rfp import RfpRecord

    return RfpRecord(
        id="rfp_0001",
        title="Island County Tourism",
        client="Island County",
        sector="tourism",
        source="manual",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-01",
        lastActivityNote="test",
    )


class ChatRawMessageRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_intent_receives_raw_ask_not_evidence_stanza(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp_0001",
            updatedAt="2026-08-10T00:00:00+00:00",
            sections=[_section()],
        )
        seen: dict[str, str] = {}

        async def capture_classify(**kwargs):
            seen["classify"] = kwargs["user_message"]
            return {"intent": "advisory", "primarySectionId": None, "reason": "test"}

        async def capture_structure(**kwargs):
            seen["structure"] = kwargs["user_message"]
            from app.services.proposal_chat_structure import StructurePlan

            return StructurePlan(action="clarify", clarifyQuestion="Which section?")

        async def advisory(**kwargs):
            seen["advisory"] = kwargs["user_message"]
            return "Advisory answer.", None

        ask = "what's wrong with the pricing?"
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch.object(
                editor,
                "aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch.object(
                editor, "aget_proposal_draft", new=AsyncMock(return_value=draft)
            ),
            patch.object(
                editor, "aget_research_cache", new=AsyncMock(return_value=None)
            ),
            patch(
                "app.services.proposal_chat_manuscript_fix.classify_chat_edit_intent",
                side_effect=capture_classify,
            ),
            patch(
                "app.services.proposal_chat_structure.plan_chat_structure_action",
                side_effect=capture_structure,
            ),
            patch.object(editor, "_section_chat_advisory_reply", side_effect=advisory),
        ):
            await editor.improve_proposal_section(
                "rfp_0001",
                "section-20-budget",
                ask,
                persist=False,
            )

        self.assertEqual(seen.get("classify"), ask)
        self.assertNotIn("Evidence policy", seen.get("classify", ""))
        # Advisory path returns before structure plan — still assert if reached.
        if "structure" in seen:
            self.assertEqual(seen["structure"], ask)
            self.assertNotIn("Evidence policy", seen["structure"])
        self.assertEqual(seen.get("advisory"), ask)

    async def test_structure_plan_uses_raw_ask_on_edit_path(self) -> None:
        section = ProposalSection(
            id="approach",
            title="Approach",
            content="## Approach\n\nWe will deliver the work.",
            source="generated",
            mode="write",
        )
        draft = ProposalDraft(
            rfpId="rfp_0001",
            updatedAt="2026-08-10T00:00:00+00:00",
            sections=[section],
        )
        seen: dict[str, str] = {}

        class _Stop(BaseException):
            pass

        async def capture_scope(**kwargs):
            seen["scope"] = kwargs["user_message"]
            raise _Stop()

        async def capture_structure(**kwargs):
            seen["structure"] = kwargs["user_message"]
            raise AssertionError("structure planner should not run for polish edits")

        ask = "tighten the opening paragraph"
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch.object(
                editor,
                "aload_rfp_for_proposal",
                new=AsyncMock(
                    return_value=(
                        _rfp(),
                        SimpleNamespace(description="", pdf_text=""),
                        "RFP text",
                    )
                ),
            ),
            patch.object(
                editor, "aget_proposal_draft", new=AsyncMock(return_value=draft)
            ),
            patch.object(
                editor, "aget_research_cache", new=AsyncMock(return_value=None)
            ),
            patch(
                "app.services.proposal_chat_structure.plan_chat_structure_action",
                side_effect=capture_structure,
            ),
            patch.object(editor, "_plan_edit_scope", side_effect=capture_scope),
            patch.object(
                editor,
                "_try_budget_manual_fill_handoff",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                editor,
                "_try_budget_section_canonical_refresh",
                new=AsyncMock(return_value=None),
            ),
        ):
            with self.assertRaises(_Stop):
                await editor.improve_proposal_section(
                    "rfp_0001",
                    "approach",
                    ask,
                    persist=False,
                )
        self.assertEqual(seen.get("scope"), ask)
        self.assertNotIn("Evidence policy", seen.get("scope", ""))
        self.assertNotIn("structure", seen)


class ManualFillStanzaHijackTests(unittest.IsolatedAsyncioTestCase):
    def test_evidence_stanza_does_not_trip_manual_fill_or_edit_detectors(self) -> None:
        from app.services.proposal_evidence_gate import (
            EvidenceDecision,
            EvidenceGateResult,
            evidence_policy_prompt_stanza,
        )
        from app.services.proposal_manual_flags import is_manual_fill_request
        from app.services.proposal_section_editor import _wants_section_edit

        ask = "what's wrong with the pricing?"
        for action in EvidenceDecision:
            stanza = evidence_policy_prompt_stanza(
                EvidenceGateResult(action=action, reason="t"),
                section_id="section-20-budget",
            )
            augmented = f"{stanza}\n\n{ask}"
            self.assertFalse(
                is_manual_fill_request(augmented),
                f"{action.value} stanza trips MANUAL FILL: {stanza!r}",
            )
            self.assertFalse(
                _wants_section_edit(augmented),
                f"{action.value} stanza flips edit detector: {stanza!r}",
            )
            self.assertNotIn("[MANUAL FILL]", stanza)
            self.assertNotIn("[VERIFY]", stanza)

    async def test_ordinary_ask_reaches_advisory_not_manual_fill_short_circuit(
        self,
    ) -> None:
        draft = ProposalDraft(
            rfpId="rfp_0001",
            updatedAt="2026-08-10T00:00:00+00:00",
            sections=[_section()],
        )
        seen: dict[str, bool] = {"advisory": False}

        async def capture_classify(**kwargs):
            return {"intent": "advisory", "primarySectionId": None, "reason": "test"}

        async def advisory(**kwargs):
            seen["advisory"] = True
            return "Advisory answer.", None

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch.object(
                editor,
                "aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch.object(
                editor, "aget_proposal_draft", new=AsyncMock(return_value=draft)
            ),
            patch.object(
                editor, "aget_research_cache", new=AsyncMock(return_value=None)
            ),
            patch(
                "app.services.proposal_chat_manuscript_fix.classify_chat_edit_intent",
                side_effect=capture_classify,
            ),
            patch.object(editor, "_section_chat_advisory_reply", side_effect=advisory),
        ):
            _section_out, _draft, _research, _provider, reply, changed, _fix = (
                await editor.improve_proposal_section(
                    "rfp_0001",
                    "section-20-budget",
                    "what's wrong with the pricing?",
                    persist=False,
                )
            )

        self.assertTrue(seen["advisory"])
        self.assertFalse(changed)
        self.assertIn("Advisory", reply)


class StructurePlanFailureDefaultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_failure_clarifies_instead_of_editing_open_tab(self) -> None:
        from app.services.llm import LlmError
        from app.services.proposal_chat_structure import plan_chat_structure_action

        draft = ProposalDraft(
            rfpId="rfp_0001",
            updatedAt="2026-08-10T00:00:00+00:00",
            sections=[_section()],
        )
        with patch(
            "app.services.proposal_chat_structure.llm.chat_json",
            new=AsyncMock(side_effect=LlmError("provider down")),
        ):
            # Avoid add/bio/case heuristics so the LLM path (and its failure
            # default) is what we exercise.
            plan = await plan_chat_structure_action(
                draft=draft,
                user_message="reorganize how the sidebar sections are arranged",
                focus_section_id="section-20-budget",
                rfp_title="Island County",
                rfp_client="Island County",
                rfp_context="",
            )
        self.assertEqual(plan.action, "clarify")
        self.assertNotEqual(plan.action, "edit")
        self.assertTrue((plan.clarify_question or "").strip())


if __name__ == "__main__":
    unittest.main()
