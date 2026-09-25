"""Chat-path Cost tab collapse gate (proposal_section_editor.improve_proposal_section, ~9217-9229).

Extracted to budget_chat_should_collapse_duplicate_cost_tabs (proposal_budget_playbook.py)
because reaching that line through the real chat entrypoint needs a large harness
(conversation history, draft lookup, LLM agents, ...). v2 (pricing-plan) budgets
get the collapse; frozen legacy budgets are ordinary manuscript text and must not
be collapsed/merged.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalBudget, ProposalResearchCache
from app.services.proposal_budget_playbook import (
    budget_chat_should_collapse_duplicate_cost_tabs,
)


def _research(*, pricing_plan: dict | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-gate",
        updatedAt="2026-08-05T00:00:00+00:00",
        budget=ProposalBudget(
            pricingPlan=pricing_plan,
            rfpId="rfp-gate",
            updatedAt="2026-08-05T00:00:00+00:00",
        ),
    )


class BudgetChatCollapseGateTests(unittest.TestCase):
    def test_v2_pricing_plan_budget_collapses(self) -> None:
        research = _research(pricing_plan={"tier": "Average"})
        self.assertTrue(budget_chat_should_collapse_duplicate_cost_tabs(research))

    def test_legacy_frozen_budget_does_not_collapse(self) -> None:
        research = _research(pricing_plan=None)
        self.assertFalse(budget_chat_should_collapse_duplicate_cost_tabs(research))

    def test_no_research_does_not_collapse(self) -> None:
        self.assertFalse(budget_chat_should_collapse_duplicate_cost_tabs(None))

    def test_no_budget_does_not_collapse(self) -> None:
        research = ProposalResearchCache(
            rfpId="rfp-gate", updatedAt="2026-08-05T00:00:00+00:00"
        )
        self.assertFalse(budget_chat_should_collapse_duplicate_cost_tabs(research))


if __name__ == "__main__":
    unittest.main()
