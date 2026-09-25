"""T5 — money slots, free-currency backstop."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_budget_slots import (
    find_unresolved_budget_slots,
    render_budget_slots,
    render_draft_budget_slots,
)
from app.services.proposal_consistency import scan_manuscript_consistency
from app.services.proposal_pipeline_status import collect_manuscript_blockers


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="Test RFP",
        client="Acme County",
        dueDate="2026-12-01",
        receivedDate="2026-01-01",
        lastActivity="2026-01-01T00:00:00Z",
        lastActivityNote="test",
    )


class MoneySlotTests(unittest.TestCase):
    def test_render_known_slots(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            agencyRevenueEstimate=37500.0,
            totalClientInvoicing=287500.0,
        )
        text = "Agency revenue is {{budget.agency_revenue}} of {{budget.total_client_invoicing}}."
        rendered, unresolved = render_budget_slots(text, budget)
        self.assertEqual(unresolved, [])
        self.assertIn("$37,500.00", rendered)
        self.assertIn("$287,500.00", rendered)

    def test_unresolved_slot_preserved(self) -> None:
        budget = ProposalBudget(rfpId="r1", updatedAt="t")
        rendered, unresolved = render_budget_slots(
            "Total {{budget.agency_revenue}}", budget
        )
        self.assertIn("agency_revenue", unresolved)
        self.assertIn("{{budget.agency_revenue}}", rendered)

    def test_draft_render_and_consistency_critical(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="Investment is {{budget.unknown_key}}.",
                )
            ],
        )
        draft2, unresolved = render_draft_budget_slots(draft, None)
        self.assertIn("unknown_key", unresolved)
        issues = scan_manuscript_consistency(draft=draft2, research=None, rfp=_rfp())
        self.assertTrue(any("money_slot" in i.message for i in issues))

    def test_money_slots_block_readiness(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="See {{budget.agency_revenue}}",
                )
            ],
        )
        with mock.patch("app.services.proposal_pipeline_status.settings") as settings:
            settings.t1_gates_block = False
            settings.consistency_criticals_block = False
            settings.overlap_gates_block = False
            settings.money_slots_block = True
            blockers = collect_manuscript_blockers(
                draft=draft,
                research=None,
                rfp=_rfp(),
                require_budget=False,
            )
        self.assertTrue(any("money slot" in b.lower() for b in blockers))


class FreeCurrencyBackstopTests(unittest.TestCase):
    def test_unauthorized_dollar_is_critical_outside_budget(self) -> None:
        from app.models.proposal import ProposalResearchCache

        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            agencyRevenueEstimate=10_000.0,
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="PM",
                    extended=10_000.0,
                    isManualFill=True,
                )
            ],
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="approach",
                    title="Approach",
                    content="Agency fee is $54,321.00 which is special.",
                ),
                ProposalSection(
                    id="budget",
                    title="Budget & Pricing",
                    content="Agency revenue estimate: $10,000.00",
                ),
            ],
        )
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            budget=budget,
            evidenceCorpus=[],
        )
        issues = scan_manuscript_consistency(draft=draft, research=research, rfp=_rfp())
        # Sync free_currency regex retired — labeled fee mismatch still critical.
        labeled = [
            i
            for i in issues
            if i.severity == "critical"
            and (
                "agency_fee" in (i.message or "").casefold()
                or "54,321" in (i.message or "")
                or "54321" in (i.message or "").replace(",", "")
            )
        ]
        self.assertTrue(labeled, msg=[i.message for i in issues])
        self.assertEqual(labeled[0].section_id, "approach")


if __name__ == "__main__":
    unittest.main()
