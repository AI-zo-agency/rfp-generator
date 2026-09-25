"""Gap finalizer never claims to reconcile a frozen (pre-pricing-plan) budget."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
)
from app.services import proposal_submission_gap_finalizer as fin
from app.services.proposal_rfp_compliance import ComplianceGap

_TS = "2026-09-25T00:00:00+00:00"


class FrozenBudgetFinalizerTests(unittest.TestCase):
    def test_legacy_budget_logs_frozen_and_skips_reconcile(self) -> None:
        draft = ProposalDraft(rfpId="r", sections=[], updatedAt=_TS)
        research = ProposalResearchCache(
            rfpId="r",
            updatedAt=_TS,
            budget=ProposalBudget(
                rfpId="r",
                updatedAt=_TS,
                lineItems=[BudgetLineItem(id="L1", description="Fees", category="Labor", extended=1)],
            ),
        )
        gap = ComplianceGap("b", "Budget", "budget", "m", "r", "e", "h")
        reconcile = AsyncMock(side_effect=AssertionError("must not reconcile"))
        with patch("app.services.proposal_generator.run_phase3_5_budget_reconcile", new=reconcile):
            out_draft, _r, log = asyncio.run(
                fin._maybe_reconcile_budget_from_cache(
                    "r", draft=draft, research=research, gaps=[gap]
                )
            )
        self.assertEqual(log, "budget:frozen (legacy) — not reconciled")
        self.assertIs(out_draft, draft)
        reconcile.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
