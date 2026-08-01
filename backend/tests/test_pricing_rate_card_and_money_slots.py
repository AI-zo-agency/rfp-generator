"""T5 — KB pricing rate card, binding, money slots, free-currency backstop."""

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
from app.services.pricing_rate_binding import (
    bind_budget_line_items_to_rate_card,
    collect_unbound_line_item_violations,
)
from app.services.pricing_rate_card_builder import build_pricing_rate_card_from_guide_text
from app.services.proposal_budget_slots import (
    find_unresolved_budget_slots,
    render_budget_slots,
    render_draft_budget_slots,
)
from app.services.proposal_consistency import scan_manuscript_consistency
from app.services.proposal_pipeline_status import collect_manuscript_blockers


_GUIDE_SAMPLE = """
Category 05 — Content & Digital
- 5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)
- 5.4 Monthly Digital Advertising Management (Avg: $2,500–$4,500)
Category 09 — Account & Project Management
- 9.1 Project Management short projects 3–6 months (Avg: $7,500–$12,000)
Senior Strategist $175/hr
"""


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


class PricingRateCardBuilderTests(unittest.TestCase):
    def test_extracts_menu_ranges_and_hourly(self) -> None:
        card = build_pricing_rate_card_from_guide_text(_GUIDE_SAMPLE)
        self.assertGreaterEqual(len(card.rates), 3)
        by_menu = {r.menu_id: r for r in card.rates if r.menu_id}
        self.assertIn("5.3", by_menu)
        self.assertEqual(by_menu["5.3"].amount_low, 3200.0)
        self.assertEqual(by_menu["5.3"].amount_high, 4800.0)
        self.assertAlmostEqual(by_menu["5.3"].amount or 0, 4000.0)
        hourly = [r for r in card.rates if r.unit == "hour"]
        self.assertTrue(hourly)
        self.assertEqual(hourly[0].amount, 175.0)

    def test_empty_guide_yields_warning_not_invention(self) -> None:
        card = build_pricing_rate_card_from_guide_text("(No 00_Guide_Pricing content in KB)")
        self.assertEqual(card.rates, [])
        self.assertTrue(card.warnings)


class RateBindingTests(unittest.TestCase):
    def test_confident_match_sets_source_rate_id(self) -> None:
        card = build_pricing_rate_card_from_guide_text(_GUIDE_SAMPLE)
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            pricingTier="Average",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Digital",
                    description="Monthly Social Media Management",
                    rateSource="5.3 — 00_Guide_Pricing Average",
                    rate=4000.0,
                    extended=4000.0,
                    unit="flat",
                )
            ],
            agencyRevenueEstimate=4000.0,
        )
        bound = bind_budget_line_items_to_rate_card(budget, card)
        self.assertFalse(bound.line_items[0].is_manual_fill)
        self.assertTrue(bound.line_items[0].source_rate_id)
        self.assertEqual(collect_unbound_line_item_violations(bound), [])

    def test_no_match_flags_manual_fill_without_inventing(self) -> None:
        card = build_pricing_rate_card_from_guide_text(_GUIDE_SAMPLE)
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="L9",
                    category="Mystery",
                    description="Invented holographic billboard package",
                    rate=99999.0,
                    extended=99999.0,
                )
            ],
            agencyRevenueEstimate=99999.0,
        )
        bound = bind_budget_line_items_to_rate_card(budget, card)
        item = bound.line_items[0]
        self.assertTrue(item.is_manual_fill)
        self.assertIsNone(item.source_rate_id)
        self.assertEqual(item.extended, 99999.0)  # not rewritten
        self.assertTrue(any("unbound" in f.lower() for f in bound.pricing_flags))
        self.assertEqual(collect_unbound_line_item_violations(bound), [])

    def test_unbound_xor_violation_detected(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="L0",
                    category="Fees",
                    description="Bound sibling",
                    extended=500.0,
                    isManualFill=True,
                ),
                BudgetLineItem(
                    id="L1",
                    category="Fees",
                    description="Ungrounded fee",
                    extended=1000.0,
                    isManualFill=False,
                    sourceRateId=None,
                ),
            ],
        )
        errs = collect_unbound_line_item_violations(budget)
        self.assertTrue(any("L1" in e for e in errs))


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
