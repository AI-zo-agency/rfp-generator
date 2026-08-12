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


class VerificationQueryBuildingTests(unittest.TestCase):
    """Reported: Umatilla fact-check said 'not in KB' while 03_CS_ + 06_WON_ exist.

    Cause: queries were `zö agency 3.1 — City of Umatilla… for section 3.1…`
    (section chrome + the full user question), which return 0 hybrid hits.
    """

    def test_strips_section_number_and_chat_chrome(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="section-3-work-umatilla",
            title="3.1 — City of Umatilla Digital Campaign 2006",
            content=(
                "Rock the Lock Music Festival\n\n"
                "SOLUTION / OUR APPROACH\n"
                "We delivered the campaign in two weeks.\n"
            ),
            mode="write",
            word_target=350,
        )
        queries = editor._build_verification_kb_queries(
            section=section,
            user_message=(
                "for section 3.1 City of Umatilla can you just cross verify if "
                "everything is truth or any fabiracted values?? there?"
            ),
            excerpt="",
            rfp_client="Island County",
            rfp_sector="tourism",
            rfp_title="Social Media Management",
        )
        blob = " | ".join(queries).casefold()
        self.assertTrue(queries)
        self.assertIn("umatilla", blob)
        self.assertTrue(
            "rock the lock" in blob or "rock the locks" in blob,
            queries,
        )
        for q in queries:
            self.assertNotRegex(q, r"\b3\.1\b")
            self.assertNotIn("cross verify", q.casefold())
            self.assertNotIn("fabiracted", q.casefold())

    def test_verify_and_fabrication_asks_match(self) -> None:
        self.assertTrue(
            editor._is_verification_only_ask(
                "cross verify if everything is truth or any fabiracted values?"
            )
        )
        self.assertTrue(
            editor._is_verification_only_ask(
                "are there any fabricated numbers in this case study?"
            )
        )
        self.assertFalse(
            editor._is_verification_only_ask("remove fabricated content from the draft")
        )


class VerificationChunkFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_chunk_when_memory_empty(self) -> None:
        """Case-study PDF hits often have empty `memory` but full `chunk`/`content`."""

        async def fake_search(**kwargs):
            return [
                {
                    "memory": "",
                    "chunk": "# City of Umatilla Rock The Locks\n\nAgency of record.",
                    "similarity": 0.81,
                    "metadata": {
                        "fileName": "03_CS_City of Umatilla_Digital Campaign_2006.pdf"
                    },
                }
            ]

        with patch.object(editor.supermemory, "search_hybrid", side_effect=fake_search):
            block = await editor._verification_facts_block(
                ["zö agency City of Umatilla case study"],
                prefer_needles=["City of Umatilla", "Rock the Lock"],
            )
        self.assertIn("Umatilla", block)
        self.assertIn("03_CS_City of Umatilla", block)

    async def test_prefers_needle_matching_hits_over_noise(self) -> None:
        async def fake_search(**kwargs):
            return [
                {
                    "memory": "City of Lake Oswego accepted proposal from zö agency.",
                    "similarity": 0.90,
                    "metadata": {"fileName": "07_FIN_CityofLakeOswego_Proposal_2026.pdf"},
                },
                {
                    "memory": "",
                    "chunk": "zö agency is the agency of record for the Rock the Locks festival.",
                    "similarity": 0.88,
                    "metadata": {"fileName": "06_WON_CityofUmatilla_Proposal_2026.pdf"},
                },
            ]

        with patch.object(editor.supermemory, "search_hybrid", side_effect=fake_search):
            block = await editor._verification_facts_block(
                ["zö agency City of Umatilla"],
                prefer_needles=["Umatilla", "Rock the Locks"],
            )
        self.assertIn("Rock the Locks", block)
        self.assertIn("Umatilla", block)
        self.assertLess(block.index("Rock the Locks"), block.index("Lake Oswego"))


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

        async def fake_facts(queries, **kwargs):
            if facts == "RAISE":
                raise RuntimeError("down")
            return facts

        async def fake_blob(queries, **kwargs):
            return blob, ""

        async def fake_plan(**kwargs):
            return ["zö agency legal name 01 companyfacts"]

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
            patch.object(editor, "_plan_verification_kb_queries", side_effect=fake_plan),
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

    async def test_pinned_excerpt_wrong_triggers_kb_lookup(self) -> None:
        """'here its wrong' + pinned excerpt must query KB via the planner."""
        from app.models.proposal import ProposalSection
        from app.models.rfp import RfpRecord

        captured: dict[str, str] = {}

        async def fake_chat(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return {"reply": "ok"}, "stub"

        async def fake_plan(**kwargs):
            return ["01_companyfacts verified email zö agency"]

        async def fake_facts(queries, **kwargs):
            return "- Email: connect@zo.agency  [source: 01_companyfacts verified.docx]"

        section = ProposalSection(
            id="1.3", title="1.3 — Business Information",
            content="Email | info@zo.agency", mode="write", word_target=187,
        )
        rfp = RfpRecord(
            id="r1", title="Website Redesign", client="DuPage", sector="Government",
            source="manual", dueDate="2026-09-01", receivedDate="2026-08-01",
            lastActivity="2026-08-01", lastActivityNote="t",
        )
        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_plan_verification_kb_queries", side_effect=fake_plan),
            patch.object(editor, "_verification_facts_block", side_effect=fake_facts),
            patch.object(editor, "_fetch_kb_blob_for_selection", return_value=("", "")),
        ):
            await editor._section_chat_advisory_reply(
                section=section, rfp=rfp, rfp_context="",
                user_message="here its wrong",
                conversation_history=[],
                selection_text="Email | info@zo.agency",
                requirements_block="",
            )
        prompt = captured["user"]
        self.assertIn("Verified KB facts", prompt)
        self.assertIn("connect@zo.agency", prompt)

    async def test_pinned_wrong_uses_query_planner(self) -> None:
        from app.models.proposal import ProposalSection
        from app.models.rfp import RfpRecord

        planned: list[str] = []

        async def fake_plan(**kwargs):
            planned.append(kwargs.get("user_message") or "")
            return ["01_companyfacts verified email zö agency"]

        async def fake_facts(queries, **kwargs):
            return "- Email: connect@zo.agency  [source: 01_companyfacts verified.docx]"

        async def fake_chat(messages, **kwargs):
            return {"reply": "Incorrect — use connect@zo.agency per companyfacts."}, "stub"

        section = ProposalSection(
            id="1.3", title="1.3 — Business Information",
            content="Email | info@zo.agency", mode="write", word_target=187,
        )
        rfp = RfpRecord(
            id="r1", title="Website Redesign", client="DuPage", sector="Government",
            source="manual", dueDate="2026-09-01", receivedDate="2026-08-01",
            lastActivity="2026-08-01", lastActivityNote="t",
        )
        with (
            patch.object(editor, "chat_json_with_repair", side_effect=fake_chat),
            patch.object(editor, "_plan_verification_kb_queries", side_effect=fake_plan),
            patch.object(editor, "_verification_facts_block", side_effect=fake_facts),
            patch.object(editor, "_fetch_kb_blob_for_selection", return_value=("", "")),
        ):
            await editor._section_chat_advisory_reply(
                section=section, rfp=rfp, rfp_context="",
                user_message="here its wrong",
                conversation_history=[],
                selection_text="Email | info@zo.agency",
                requirements_block="",
            )
        self.assertTrue(planned, "query planner must run for pinned excerpt wrong-ask")
        self.assertIn("here its wrong", planned[0])


class VerificationQueryPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_merges_heuristic_and_agent_queries(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="1.3", title="1.3 — Business Information",
            content="Email | info@zo.agency", mode="write", word_target=187,
        )

        async def fake_plan(**kwargs):
            return ["01_companyfacts verified email connect zö agency"]

        with (
            patch.object(editor.llm, "is_configured", return_value=True),
            patch(
                "app.services.proposal_langchain_agents.plan_section_queries_agent",
                side_effect=fake_plan,
            ),
        ):
            queries = await editor._plan_verification_kb_queries(
                section=section,
                user_message="here its wrong",
                excerpt="Email | info@zo.agency",
                rfp_client="DuPage",
                rfp_sector="Government",
                rfp_title="RFP",
            )
        self.assertTrue(queries)
        self.assertTrue(
            any("companyfacts" in q.casefold() for q in queries),
            queries,
        )


class CompanyfactsContactPinTests(unittest.TestCase):
    def test_prefers_verified_docx_over_md(self) -> None:
        facts = (
            "- Email: hello@zo.agency  [source: 01_companyfacts.md]\n"
            "- Email: connect@zo.agency  [source: 01_companyfacts verified.docx]"
        )
        pin = editor._extract_companyfacts_contact_pin(facts)
        self.assertIn("connect@zo.agency", pin)
        self.assertNotIn("hello@zo.agency", pin)


class VerificationFactsRankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_companyfacts_ranked_first(self) -> None:
        async def fake_search(**kwargs):
            return [
                {
                    "memory": "Email: hello@zo.agency",
                    "similarity": 0.92,
                    "metadata": {"fileName": "01_companyfacts.md"},
                },
                {
                    "memory": "Email: connect@zo.agency",
                    "similarity": 0.90,
                    "metadata": {"fileName": "01_companyfacts verified.docx"},
                },
            ]

        with patch.object(editor.supermemory, "search_hybrid", side_effect=fake_search):
            block = await editor._verification_facts_block(
                ["01_companyfacts verified email zö agency"]
            )
        self.assertLess(block.index("connect@"), block.index("hello@"))


if __name__ == "__main__":
    unittest.main()
