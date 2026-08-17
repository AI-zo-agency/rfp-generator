"""Cost Proposal section chat must refresh from canonical budget, not invent rates."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_section_editor import _try_budget_section_canonical_refresh


class BudgetSectionChatGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_cost_tab_single_edit_refreshes_from_canonical_budget(self) -> None:
        section = ProposalSection(
            id="section-cost",
            title="7. Cost Proposal",
            content="Invented Senior Strategist $175/hr per 00_Guide_Pricing.",
            status="generated",
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[section],
        )
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="phased",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="1.1 Stakeholder Interviews",
                    unit="flat",
                    quantity=1,
                    rate=7000,
                    extended=7000,
                    lineItemType="agency_fee",
                )
            ],
            agencyRevenueEstimate=7000,
            lumpSumTotal=7000,
        )
        from app.models.proposal import ProposalResearchCache

        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            budget=budget,
        )

        with patch(
            "app.services.proposal_section_editor._persist_section_improve_draft",
            new_callable=AsyncMock,
            side_effect=lambda d, r, **kw: d,
        ):
            result = await _try_budget_section_canonical_refresh(
                rfp_id="r1",
                section=section,
                section_id=section.id,
                draft=draft,
                research=research,
                user_message="Be in more detailed breakdown of deliverables",
                chat_intent="single_edit",
                conversation_history=[],
                rfp_text="Pricing shall remain fixed. Pricing Table.",
                persist=True,
                selection_mode=False,
            )

        self.assertIsNotNone(result)
        focus, updated_draft, _, _, reply, changed = result
        self.assertTrue(changed)
        self.assertIn("canonical Stage 3.5", reply)
        self.assertNotIn("/hr", focus.content or "")
        self.assertNotIn("00_Guide", focus.content or "")
        self.assertIn("Stakeholder Interviews", focus.content or "")
        self.assertEqual(
            (updated_draft.sections[0].content or ""),
            focus.content,
        )

    async def test_rebuild_ask_skips_canonical_refresh(self) -> None:
        section = ProposalSection(
            id="section-cost",
            title="7. Cost Proposal",
            content="Old",
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        result = await _try_budget_section_canonical_refresh(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=None,
            user_message="rebuild Cost Proposal from the pricing guide",
            chat_intent="single_edit",
            conversation_history=[],
            rfp_text="",
            persist=False,
            selection_mode=False,
        )
        self.assertIsNone(result)

    async def test_confirm_before_submit_skips_canonical_refresh(self) -> None:
        section = ProposalSection(
            id="section-cost",
            title="7. Cost Proposal",
            content="[MANUAL FILL: media base — confirm before submission]",
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        result = await _try_budget_section_canonical_refresh(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=None,
            user_message="fill all confirm before submit thing",
            chat_intent="single_edit",
            conversation_history=[],
            rfp_text="",
            persist=False,
            selection_mode=False,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
