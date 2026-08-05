"""What actually reaches the excerpt rewriter's prompt.

Nothing asserted this before, which is how rfp_context[:2000] could silently
drop the HARD FACTS, mapped requirements, manuscript digest and pricing guide
that improve_proposal_section() goes to the trouble of assembling.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_section_editor as editor

HARD_FACTS = (
    "## HARD FACTS (from full RFP text — cite exactly; never invent 'undisclosed')\n"
    "### Contract value / ceiling\n- Not-to-exceed $250,000 over three years.\n"
)
REQUIREMENTS = (
    "--- Mapped section requirements ---\n"
    "- Provide legal name, DBA, EIN, and years in operation.\n"
)
DIGEST = (
    "FULL PROPOSAL MANUSCRIPT (every section — use this for whole-proposal answers):\n"
    "\n### 1.1 Cover Letter\nWe are pleased to submit our qualifications.\n"
)
PRICING = "=== 00_Guide_Pricing (Supermemory) ===\nCreative Director $185/hr.\n"

RFP_CONTEXT = "\n\n".join(
    [
        "Title: Kalamazoo Valley CC RFP\nClient: KVCC\n\n" + ("RFP boilerplate. " * 4000),
        HARD_FACTS,
        REQUIREMENTS,
        DIGEST,
        PRICING,
    ]
)

SECTION_BODY = (
    "BUSINESS INFORMATION\n\n"
    "**Legal Name:** Z'Onion Creative Group LLC\n\n"
    "**Years in Operation:** [VERIFY: years in operation]\n"
)
EXCERPT = "**Years in Operation:** [VERIFY: years in operation]"


def _section() -> ProposalSection:
    return ProposalSection(
        id="1.3",
        title="Business Information",
        content=SECTION_BODY,
        mode="write",
        word_target=200,
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp_0001",
        title="Kalamazoo Valley CC RFP",
        client="KVCC",
        sector="Education",
        source="manual",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-01",
        lastActivityNote="test",
    )


class SelectionPromptContextTests(unittest.IsolatedAsyncioTestCase):
    async def _capture_rewrite_prompt(self) -> str:
        """Run the excerpt rewriter with a stub LLM and return its user block."""
        captured: dict[str, str] = {}

        async def fake_chat_json(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return {"replacement": "**Years in Operation:** 13 years"}, "stub"

        content = _section().content or ""
        start = content.index(EXCERPT)
        with patch.object(editor.llm, "chat_json", side_effect=fake_chat_json):
            await editor._improve_section_selection(
                section=_section(),
                rfp=_rfp(),
                rfp_context=RFP_CONTEXT,
                user_message="Fill the years in operation gap from the knowledge base.",
                selection_start=start,
                selection_end=start + len(EXCERPT),
                selection_text=EXCERPT,
                brand_voice=None,
                kb_zo_voice="",
                kb_block="01_companyfacts: Founded August 21, 2013 in Bend, Oregon.",
                fact_blob="01_companyfacts: Founded August 21, 2013 in Bend, Oregon.",
                lean=False,
            )
        return captured["user"]

    async def test_hard_facts_reach_the_rewriter(self) -> None:
        prompt = await self._capture_rewrite_prompt()
        self.assertIn("Not-to-exceed $250,000", prompt)

    async def test_mapped_requirements_reach_the_rewriter(self) -> None:
        prompt = await self._capture_rewrite_prompt()
        self.assertIn("Provide legal name, DBA, EIN", prompt)

    async def test_manuscript_digest_reaches_the_rewriter(self) -> None:
        prompt = await self._capture_rewrite_prompt()
        self.assertIn("FULL PROPOSAL MANUSCRIPT", prompt)

    async def test_kb_excerpts_still_reach_the_rewriter(self) -> None:
        prompt = await self._capture_rewrite_prompt()
        self.assertIn("Founded August 21, 2013", prompt)

    async def test_excerpt_and_section_still_reach_the_rewriter(self) -> None:
        prompt = await self._capture_rewrite_prompt()
        self.assertIn(EXCERPT, prompt)
        self.assertIn("Z'Onion Creative Group LLC", prompt)


class SelectionPlannerContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_sees_what_the_rfp_requires(self) -> None:
        """Otherwise its kbQueries are written against the excerpt alone."""
        captured: dict[str, str] = {}

        async def fake_chat_json(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            captured["node_name"] = kwargs.get("node_name") or ""
            return {"editorInstruction": "Fill the gap.", "kbQueries": ["01 companyfacts founded"]}, "stub"

        with patch.object(editor.llm, "chat_json", side_effect=fake_chat_json):
            await editor._plan_selection_edit(
                section=_section(),
                rfp=_rfp(),
                user_message="Fill the years in operation gap.",
                excerpt=EXCERPT,
                full_content=SECTION_BODY,
                selection_start=0,
                selection_end=len(EXCERPT),
                rfp_context=RFP_CONTEXT,
            )

        self.assertIn("Provide legal name, DBA, EIN", captured["user"])
        self.assertIn("Not-to-exceed $250,000", captured["user"])
        # The planner picks queries; it must not be routed as an unnamed node.
        self.assertEqual(captured["node_name"], "chat_selection_kb_plan")

    async def test_planner_without_rfp_context_still_works(self) -> None:
        async def fake_chat_json(messages, **kwargs):
            return {"editorInstruction": "Fill the gap.", "kbQueries": []}, "stub"

        with patch.object(editor.llm, "chat_json", side_effect=fake_chat_json):
            instruction, queries = await editor._plan_selection_edit(
                section=_section(),
                rfp=_rfp(),
                user_message="Fill the gap.",
                excerpt=EXCERPT,
                full_content=SECTION_BODY,
                selection_start=0,
                selection_end=len(EXCERPT),
            )
        self.assertIn("Fill the gap", instruction)
        self.assertEqual(queries, [])


if __name__ == "__main__":
    unittest.main()
