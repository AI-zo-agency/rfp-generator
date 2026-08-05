"""Verification asks need crisp facts with provenance, not full-document dumps.

Reported case: asked to verify "Z'Onion" vs "Zohman", the assistant answered
"I have no source documents in the knowledge base" — while Supermemory held
"Organization legal name: Z'Onion Creative Group LLC DBA zö agency" in
01_companyfacts verified.docx at 0.99 similarity.

_fetch_kb_blob_for_selection expands hits into whole documents, so the precise
memory and its filename were replaced by an awards/logo OCR dump.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import proposal_section_editor as editor

HITS = [
    {
        "memory": "Organization legal name: Z'Onion Creative Group LLC DBA zö agency",
        "similarity": 0.9336695325,
        "metadata": {"fileName": "01_companyfacts verified.docx", "docKind": "companyfacts verified"},
    },
    {
        "memory": "Legal name: Z'Onion Creative Group LLC",
        "similarity": 0.926,
        "metadata": {"fileName": "01_companyfacts verified.docx"},
    },
    {
        "memory": "Federal LLC ID: 47-4333943",
        "similarity": 0.741,
        "metadata": {"fileName": "01_companyfacts verified.docx"},
    },
    {
        "memory": "A black and white logo featuring the letters 'MER' in a circular shape.",
        "similarity": 0.31,
        "metadata": {"fileName": "05_awards.docx"},
    },
]


class VerificationFactsBlockTests(unittest.IsolatedAsyncioTestCase):
    async def _block(self, hits=None):
        async def fake_search(**kwargs):
            return HITS if hits is None else hits

        with patch.object(editor.supermemory, "search_hybrid", side_effect=fake_search):
            return await editor._verification_facts_block(["zö agency legal name"])

    async def test_keeps_the_precise_fact(self) -> None:
        block = await self._block()
        self.assertIn("Organization legal name: Z'Onion Creative Group LLC", block)

    async def test_names_the_source_document(self) -> None:
        """Without provenance the model cannot honour "name its source doc"."""
        block = await self._block()
        self.assertIn("01_companyfacts", block)

    async def test_drops_low_similarity_noise(self) -> None:
        block = await self._block()
        self.assertNotIn("black and white logo", block)

    async def test_no_hits_returns_empty(self) -> None:
        self.assertEqual(await self._block(hits=[]), "")

    async def test_search_failure_returns_empty_not_raise(self) -> None:
        async def boom(**kwargs):
            raise RuntimeError("supermemory down")

        with patch.object(editor.supermemory, "search_hybrid", side_effect=boom):
            self.assertEqual(await editor._verification_facts_block(["q"]), "")

    async def test_duplicate_memories_appear_once(self) -> None:
        block = await self._block(hits=[HITS[0], HITS[0], HITS[1]])
        self.assertEqual(block.count("Organization legal name"), 1)


class AdvisoryUsesVerifiedFactsTests(unittest.IsolatedAsyncioTestCase):
    """The verified-facts block must reach the prompt, and a KB outage must be
    stated as an outage rather than reported as "no documents exist"."""

    async def _prompt(self, *, facts: str, blob: str = "") -> str:
        from app.models.proposal import ProposalSection
        from app.models.rfp import RfpRecord

        captured: dict[str, str] = {}

        async def fake_chat(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return {"reply": "ok"}, "stub"

        async def fake_facts(queries):
            if facts == "RAISE":
                raise RuntimeError("down")
            return facts

        async def fake_blob(queries, **kwargs):
            return blob, ""

        section = ProposalSection(
            id="1.3", title="Business Information",
            content="**Legal Name:** Z'Onion Creative Group LLC", mode="write", word_target=187,
        )
        rfp = RfpRecord(
            id="r1", title="Website Redesign", client="San Benito", sector="Government",
            source="manual", dueDate="2026-09-01", receivedDate="2026-08-01",
            lastActivity="2026-08-01", lastActivityNote="t",
        )
        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_verification_facts_block", side_effect=fake_facts),
            patch.object(editor, "_fetch_kb_blob_for_selection", side_effect=fake_blob),
        ):
            await editor._section_chat_advisory_reply(
                section=section, rfp=rfp, rfp_context="",
                user_message="can u verify is it Z'Onion or Zohman?",
                conversation_history=[],
                selection_text="**Legal Name:** Z'Onion Creative Group LLC",
                requirements_block="",
            )
        return captured["user"]

    async def test_verified_facts_reach_the_prompt(self) -> None:
        prompt = await self._prompt(
            facts="- Organization legal name: Z'Onion Creative Group LLC DBA zö agency [01_companyfacts verified.docx]"
        )
        self.assertIn("Organization legal name", prompt)
        self.assertIn("01_companyfacts", prompt)

    async def test_outage_is_labelled_as_an_outage(self) -> None:
        prompt = await self._prompt(facts="RAISE")
        self.assertIn("could not be reached", prompt.lower())

    async def test_genuinely_empty_kb_says_nothing_found(self) -> None:
        prompt = await self._prompt(facts="")
        self.assertIn("no matching", prompt.lower())


if __name__ == "__main__":
    unittest.main()
