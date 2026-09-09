"""Cost tab chat: narrative freeform allowed; fee mutations stay canonical."""

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
from app.services.proposal_budget_playbook import (
    apply_budget_freeform_postprocess,
    budget_ask_allows_freeform_narrative,
    refuse_noncompliant_budget_edit,
    user_asks_budget_fee_structure_mutation,
    user_asks_budget_narrative_freeform,
)
from app.services.proposal_section_editor import _try_budget_section_canonical_refresh


def _budget() -> ProposalBudget:
    return ProposalBudget(
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


class BudgetFreeformIntentTests(unittest.TestCase):
    def test_narrative_asks_allow_freeform(self) -> None:
        self.assertTrue(
            budget_ask_allows_freeform_narrative(
                "Remove the Investment Framing table and keep Fee Detail only"
            )
        )
        self.assertTrue(
            user_asks_budget_narrative_freeform(
                "Make the Scope column more detailed for deliverables"
            )
        )
        self.assertFalse(
            user_asks_budget_fee_structure_mutation(
                "Remove the Investment Framing table and keep Fee Detail only"
            )
        )

    def test_fee_mutation_blocks_freeform(self) -> None:
        self.assertTrue(
            user_asks_budget_fee_structure_mutation(
                "Add hourly rates for Senior Strategist at $175/hr"
            )
        )
        self.assertFalse(
            budget_ask_allows_freeform_narrative(
                "Add hourly rates for Senior Strategist at $175/hr"
            )
        )

    def test_refuse_invented_dollars(self) -> None:
        prior = "## Fee Detail\n| Phase | Fee |\n| A | $7000 |\n"
        new = prior + "\nAlso add contingency $45000.\n"
        msg = refuse_noncompliant_budget_edit(
            "improve writing",
            new,
            prior_text=prior,
            budget=_budget(),
        )
        self.assertIsNotNone(msg)
        self.assertIn("45000", msg or "")

    def test_postprocess_drops_mix_table(self) -> None:
        body = (
            "### Investment Framing\n\n"
            "| Component | Share | Amount | Notes |\n"
            "| --- | ---: | ---: | --- |\n"
            "| Creative | 50% | $3500 | |\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n"
        )
        out, logs = apply_budget_freeform_postprocess(body, budget=_budget())
        self.assertNotIn("| Component | Share | Amount |", out)
        self.assertIn("## Fee Detail by Phase", out)
        self.assertTrue(logs)

    def test_postprocess_restores_stripped_hourly_schedule(self) -> None:
        prior = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n\n"
            "## Hourly Rate Schedule by Classification\n\n"
            "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase |\n"
            "| --- | ---: | ---: |\n"
            "| Account Manager | $275 | — |\n"
            "| Agency Director | $400 | — |\n"
        )
        wiped = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n\n"
            "## Hourly Rate Schedule by Classification\n\n"
            "Confirm before submit — complete hourly rate schedule by classification.\n"
        )
        out, logs = apply_budget_freeform_postprocess(
            wiped, budget=_budget(), prior_text=prior
        )
        self.assertTrue(any("Hourly Rate Schedule" in x for x in logs))
        self.assertIn("| Account Manager | $275 |", out)
        self.assertIn("| Agency Director | $400 |", out)
        self.assertNotIn("complete hourly rate schedule by classification", out.casefold())

    def test_normalize_hourly_schedule_strips_manual_fill_name_cells(self) -> None:
        from app.models.proposal import VerifiedRate

        body = (
            "## Hourly Rate Schedule by Classification\n\n"
            "| Role / Labor Category | Team Member Name | Hourly Rate (billable) | Year-2 % Increase | Year-3 % Increase |\n"
            "| --- | --- | ---: | ---: | ---: |\n"
            "| Account Manager | Jax Lai / [MANUAL FILL: fabricated name removed] | $275 | | |\n"
            "| Team member on this engagement | Sonja Anderson | $400 |  |  |\n"
            "| Art Director | [MANUAL FILL: name not in KB org chart] | $275 | | |\n"
        )
        budget = _budget()
        budget = budget.model_copy(
            update={
                "verified_rates": [
                    VerifiedRate(
                        personName="", role="Account Manager", hourlyRate=275, source="x"
                    ),
                    VerifiedRate(
                        personName="", role="Agency Director", hourlyRate=400, source="x"
                    ),
                    VerifiedRate(
                        personName="", role="Art Director", hourlyRate=275, source="x"
                    ),
                ]
            }
        )
        out, logs = apply_budget_freeform_postprocess(body, budget=budget, prior_text=body)
        self.assertTrue(logs)
        self.assertIn("| Account Manager | — | $275 | — | — |", out)
        self.assertIn("| Agency Director | Sonja Anderson | $400 | — | — |", out)
        self.assertIn("| Art Director | — | $275 | — | — |", out)
        self.assertNotIn("MANUAL FILL", out)
        self.assertNotIn("Team member on this engagement", out)
        self.assertNotIn("Jax Lai", out)


class BudgetSectionChatGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_fee_mutation_refreshes_from_canonical_budget(self) -> None:
        section = ProposalSection(
            id="section-cost",
            title="7. Cost Proposal",
            content="Invented Senior Strategist $175/hr per 00_Guide_Pricing.",
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        from app.models.proposal import ProposalResearchCache

        research = ProposalResearchCache(rfpId="r1", updatedAt="t", budget=_budget())

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
                user_message="Add hourly rates from the pricing guide",
                chat_intent="single_edit",
                conversation_history=[],
                rfp_text="Pricing shall remain fixed. Pricing Table.",
                persist=True,
                selection_mode=False,
            )

        self.assertIsNotNone(result)
        focus, _, _, _, reply, changed = result
        self.assertTrue(changed)
        self.assertIn("canonical Stage 3.5", reply)
        self.assertNotIn("/hr", focus.content or "")
        self.assertIn("Stakeholder Interviews", focus.content or "")

    async def test_layout_ask_skips_canonical_refresh(self) -> None:
        """Any non-fee-dollar ask falls through to freeform LLM — no layout allowlist."""
        section = ProposalSection(
            id="section-cost",
            title="Cost, rate structure, and overall value",
            content=(
                "## Hourly Rate Schedule by Classification\n\n"
                "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase |\n"
                "| --- | ---: | ---: |\n"
                "| Account Manager | $275 | — |\n"
            ),
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        from app.models.proposal import ProposalResearchCache

        research = ProposalResearchCache(rfpId="r1", updatedAt="t", budget=_budget())
        result = await _try_budget_section_canonical_refresh(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=research,
            user_message="Here also add in one column name of each role",
            chat_intent="single_edit",
            conversation_history=[],
            rfp_text="",
            persist=False,
            selection_mode=False,
        )
        self.assertIsNone(result)
        self.assertTrue(
            budget_ask_allows_freeform_narrative(
                "Here also add in one column name of each role"
            )
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


if __name__ == "__main__":
    unittest.main()
