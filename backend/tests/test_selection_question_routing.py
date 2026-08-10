"""A pinned excerpt says WHAT the user means, not that they want it rewritten.

Reported case: with "**Legal Name:** Z'Onion Creative Group LLC" pinned, the user
asked "can you verify if it is Z'Onion?" and the assistant rewrote the line
(140 -> 143 words) instead of answering. decide_chat_route() returned
advisory=False on its first line for every pinned turn, so the question gate
further down was never reached.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_section_editor as editor
from app.services.proposal_section_editor import decide_chat_route

EXCERPT = "**Legal Name:** Z'Onion Creative Group LLC"

# Questions about a pinned excerpt: answer, never mutate the draft.
ASKS = [
    "can you verify if it is Z'Onion?",
    "is this correct?",
    "verify the legal name",
    "can you confirm the EIN?",
    "double check this address",
    "does this match the RFP?",
    "why is this here?",
    "what does this mean?",
    "are you sure about the founding date?",
    "is Z'Onion Creative Group LLC right?",
    "cross-check this against the knowledge base",
]

# Instructions about a pinned excerpt: still edit, exactly as before.
EDITS = [
    "make this shorter",
    "fix the legal name",
    "add DBA zö agency",
    "remove this",
    "fill the gaps from the KB",
    "tighten this up",
    "can you shorten this?",
    "verify this and then fix it",
    "check this and correct it",
    "rewrite in first person",
    "use the full legal name here",
]


class SelectionRouteTests(unittest.TestCase):
    def _route(self, message: str):
        return decide_chat_route(
            chat_intent="none", user_message=message, selection_mode=True
        )

    def test_reported_case_answers_instead_of_editing(self) -> None:
        route = self._route("can you verify if it is Z'Onion?")
        self.assertTrue(route.advisory, f"routed as edit: {route.reason}")

    def test_questions_about_a_pinned_excerpt_are_advisory(self) -> None:
        for message in ASKS:
            with self.subTest(message=message):
                self.assertTrue(self._route(message).advisory)

    def test_instructions_about_a_pinned_excerpt_still_edit(self) -> None:
        for message in EDITS:
            with self.subTest(message=message):
                self.assertFalse(self._route(message).advisory)

    def test_classifier_edit_intent_overrides_a_question_shape(self) -> None:
        route = decide_chat_route(
            chat_intent="single_edit",
            user_message="is this correct?",
            selection_mode=True,
        )
        self.assertFalse(route.advisory)

    def test_short_confirmation_after_a_proposed_edit_applies_it(self) -> None:
        route = decide_chat_route(
            chat_intent="none",
            user_message="yes",
            selection_mode=True,
            conversation_history=[
                {"role": "assistant", "content": "I can update the legal name. Shall I apply it?"}
            ],
        )
        self.assertFalse(route.advisory)

    def test_non_selection_routing_is_unchanged(self) -> None:
        self.assertTrue(
            decide_chat_route(
                chat_intent="none", user_message="is this correct?", selection_mode=False
            ).advisory
        )
        self.assertFalse(
            decide_chat_route(
                chat_intent="none", user_message="fix the legal name", selection_mode=False
            ).advisory
        )


def _section() -> ProposalSection:
    return ProposalSection(
        id="1.3",
        title="Business Information",
        content=f"BUSINESS INFORMATION\n\n{EXCERPT} DBA zö agency\n",
        mode="write",
        word_target=187,
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp_0001",
        title="Website Redesign for San Benito Transportation Agencies",
        client="San Benito",
        sector="Government",
        source="manual",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-01",
        lastActivityNote="test",
    )


class AdvisoryKnowledgeBaseTests(unittest.IsolatedAsyncioTestCase):
    """A verification question must be answered against the KB, not the draft.

    Without retrieval the model can only restate the line it was asked to check,
    which reads as confirmation while proving nothing.
    """

    async def _reply_prompt(self, message: str, *, kb: str = "01_companyfacts: Legal name Z'Onion Creative Group LLC, DBA zö agency.") -> str:
        captured: dict[str, str] = {}

        async def fake_chat(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return {"reply": "Confirmed against 01_companyfacts."}, "stub"

        async def fake_kb(queries, **kwargs):
            captured["queries"] = "\n".join(queries)
            return kb, ""

        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_fetch_kb_blob_for_selection", side_effect=fake_kb),
        ):
            await editor._section_chat_advisory_reply(
                section=_section(),
                rfp=_rfp(),
                rfp_context="Title: Website Redesign\nClient: San Benito",
                user_message=message,
                conversation_history=[],
                selection_text=EXCERPT,
                requirements_block="",
            )
        return captured.get("user", "")

    async def test_verification_question_pulls_the_knowledge_base(self) -> None:
        prompt = await self._reply_prompt("can you verify if it is Z'Onion?")
        self.assertIn("Z'Onion Creative Group LLC, DBA zö agency", prompt)

    async def test_pinned_excerpt_reaches_the_advisory_prompt(self) -> None:
        prompt = await self._reply_prompt("can you verify if it is Z'Onion?")
        self.assertIn("Z'Onion Creative Group", prompt)

    async def test_non_factual_question_skips_retrieval(self) -> None:
        """Opinion asks should not pay for a KB fan-out."""
        called: dict[str, bool] = {"kb": False}

        async def fake_chat(messages, **kwargs):
            return {"reply": "Because it reads better."}, "stub"

        async def fake_kb(queries, **kwargs):
            called["kb"] = True
            return "", ""

        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_fetch_kb_blob_for_selection", side_effect=fake_kb),
        ):
            await editor._section_chat_advisory_reply(
                section=_section(),
                rfp=_rfp(),
                rfp_context="",
                user_message="why is this paragraph ordered this way?",
                conversation_history=[],
                selection_text=EXCERPT,
                requirements_block="",
            )
        self.assertFalse(called["kb"])

    async def test_kb_failure_still_produces_an_answer(self) -> None:
        async def fake_chat(messages, **kwargs):
            return {"reply": "I could not reach the knowledge base."}, "stub"

        async def boom(queries, **kwargs):
            raise RuntimeError("supermemory down")

        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_fetch_kb_blob_for_selection", side_effect=boom),
        ):
            reply, _suggested = await editor._section_chat_advisory_reply(
                section=_section(),
                rfp=_rfp(),
                rfp_context="",
                user_message="can you verify the EIN?",
                conversation_history=[],
                selection_text=EXCERPT,
                requirements_block="",
            )
        self.assertTrue(reply)


class SelectionQuestionEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """The reported bug, driven through improve_proposal_section().

    The unit-level route fix was not enough on its own: improve_proposal_section
    prepends the evidence-gate stanza onto `user_message` before routing, so the
    router was classifying prompt scaffolding ("...do not fabricate") instead of
    what the user typed, and every question came back out as an edit.
    """

    async def _run(self, message: str):
        from unittest.mock import AsyncMock

        from app.models.proposal import ProposalDraft
        from app.services.proposal_section_editor import improve_proposal_section

        section = _section()
        draft = ProposalDraft(
            rfpId="rfp_0001",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[section],
        )
        content = section.content or ""
        start = content.index(EXCERPT)

        self.seen_message = ""

        async def advisory(**kwargs):
            self.seen_message = kwargs["user_message"]
            return ("**Correct** — 01_companyfacts gives Z'Onion Creative Group LLC.", None)

        async def boom(*args, **kwargs):
            raise AssertionError("a question must not run the excerpt rewriter")

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch.object(
                editor, "aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch.object(editor, "aget_proposal_draft", new=AsyncMock(return_value=draft)),
            patch.object(editor, "aget_research_cache", new=AsyncMock(return_value=None)),
            patch.object(
                editor, "_persist_section_improve_draft",
                new=AsyncMock(side_effect=lambda d, r, section_title="": d),
            ),
            patch.object(editor, "_section_chat_advisory_reply", side_effect=advisory),
            patch.object(editor, "_improve_section_selection", side_effect=boom),
            patch.object(editor, "_plan_selection_edit", side_effect=boom),
        ):
            return await improve_proposal_section(
                "rfp_0001",
                "1.3",
                message,
                selection_start=start,
                selection_end=start + len(EXCERPT),
                selection_text=EXCERPT,
                persist=False,
            )

    async def test_advisory_reply_receives_the_users_words_not_the_stanza(self) -> None:
        """The stanza contains the literal "[VERIFY]", which would otherwise trip
        the verification-question KB trigger on every advisory turn."""
        await self._run("can you verify if it is Z'Onion?")
        self.assertEqual(self.seen_message, "can you verify if it is Z'Onion?")
        self.assertNotIn("Evidence policy", self.seen_message)

    async def test_reported_case_answers_and_leaves_the_draft_alone(self) -> None:
        section, _draft, _research, _provider, reply, changed, _fix = await self._run(
            "can you verify if it is Z'Onion?"
        )
        self.assertFalse(changed, "the draft was modified by a question")
        self.assertEqual(section.content, _section().content)
        self.assertIn("Correct", reply)


if __name__ == "__main__":
    unittest.main()
