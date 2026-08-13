"""Adversarial repair must retrieve KB evidence before it rewrites.

The repair prompt built in proposal_adversarial_repair._build_repair_message_for_finding
never contains the words "knowledge base" or "kb", so user_asks_kb_fetch_or_fill() —
a regex over natural-language chat text — returns False and the packed-evidence path
never fires. The loop is told "never invent fact-bound claims" while being handed no
facts. These tests pin the fix: when the planner has already decided a finding
requires targeted retrieval, evidence is fetched and injected.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    AdversarialAuditFinding,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RepairPlan,
)
from app.services.proposal_section_kb_evidence import fetch_packed_section_kb_evidence

# An ordinary section with none of the industry words the old regex gate looked for.
PLAIN_TITLE = "Project Approach"
PLAIN_CONTENT = "We will follow the schedule agreed at kickoff."


class PlainSectionGroundingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_section_is_grounded(self) -> None:
        """No title vocabulary required — every section can be grounded."""
        with patch(
            "app.services.kb_rag_retrieve.retrieve_for_question",
            new=AsyncMock(return_value=("Acme paid $4,200 in FY24.", ["facts.md"], [])),
        ):
            block, sources = await fetch_packed_section_kb_evidence(
                section_title=PLAIN_TITLE,
                section_content=PLAIN_CONTENT,
                user_message="fix the contradiction",
            )
        self.assertIn("Acme paid $4,200", block)
        self.assertEqual(sources, ["facts.md"])

    async def test_retrieval_failure_degrades_quietly(self) -> None:
        """Retrieval failure yields no evidence — it never raises into the repair loop."""
        with patch(
            "app.services.kb_rag_retrieve.retrieve_for_question",
            new=AsyncMock(side_effect=RuntimeError("supermemory timeout")),
        ):
            block, sources = await fetch_packed_section_kb_evidence(
                section_title=PLAIN_TITLE,
                section_content=PLAIN_CONTENT,
            )
        self.assertEqual(block, "")
        self.assertEqual(sources, [])


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(
                id="sec-approach",
                title=PLAIN_TITLE,
                content=PLAIN_CONTENT,
            )
        ],
        updatedAt="2026-08-13T00:00:00Z",
    )


def _finding() -> AdversarialAuditFinding:
    return AdversarialAuditFinding(
        severity="critical",
        category="grounding",
        code="unverified_metric",
        message="Section states a savings figure with no evidence.",
        sectionId="sec-approach",
        sectionTitle=PLAIN_TITLE,
        source="llm",
    )


def _plan(*, retrieval: bool) -> RepairPlan:
    return RepairPlan(
        findingCode="unverified_metric",
        findingCategory="grounding",
        sectionId="sec-approach",
        repairMode="targeted_rewrite",
        requiresTargetedRetrieval=retrieval,
    )


class RepairInjectsEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, *, retrieval: bool) -> str:
        """Run one repair, returning the message handed to improve_proposal_section."""
        from app.services import proposal_adversarial_repair as mod

        draft = _draft()
        improve = AsyncMock(
            return_value=(
                draft.sections[0],
                draft,
                ProposalResearchCache(
                    rfpId="rfp-1", updatedAt="2026-08-13T00:00:00Z"
                ),
                "test-provider",
                "",
                True,
                None,
            )
        )
        with (
            patch.object(mod, "improve_proposal_section", new=improve),
            patch(
                "app.services.proposal_repository.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_repository.aget_research_cache",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.kb_rag_retrieve.retrieve_for_question",
                new=AsyncMock(
                    return_value=("Acme paid $4,200 in FY24.", ["facts.md"], [])
                ),
            ),
        ):
            await mod.repair_section_for_finding(
                rfp_id="rfp-1",
                section_id="sec-approach",
                finding=_finding(),
                repair_plan=_plan(retrieval=retrieval),
                failure_reason=None,
                prior_attempt_summary="",
            )
        improve.assert_awaited_once()
        return improve.await_args.args[2]

    async def test_evidence_injected_when_plan_requires_retrieval(self) -> None:
        message = await self._run(retrieval=True)
        self.assertIn("Acme paid $4,200", message)
        self.assertIn("PACKED KB EVIDENCE", message)
        # The original repair instruction must survive alongside the evidence.
        self.assertIn("Section states a savings figure", message)

    async def test_no_evidence_when_plan_does_not_require_retrieval(self) -> None:
        message = await self._run(retrieval=False)
        self.assertNotIn("PACKED KB EVIDENCE", message)
        self.assertIn("Section states a savings figure", message)


if __name__ == "__main__":
    unittest.main()
