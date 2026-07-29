"""Commission pricing contract + Option B orphan-commission sanitizer."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.commission_budget_sanitizer import sanitize_commission_budget
from app.services.pricing_contract_builder import build_pricing_contract
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_validation import collect_orphan_commission_violations
from app.services.proposal_manual_flags import extract_manual_fill_tags


class PricingContractBuilderTests(unittest.TestCase):
    def test_evidenced_media_and_rate(self) -> None:
        stage_one = (
            "Fee model is traditional media commission (85/15). "
            "Estimated annual media placements: $250,000 at 15% agency commission."
        )
        rfp = "The County anticipates approximately $250,000 in annual paid media."
        contract = build_pricing_contract(stage_one_text=stage_one, rfp_text=rfp)
        self.assertEqual(contract.fee_model, "commission")
        self.assertEqual(contract.media_spend_annual, 250000.0)
        self.assertAlmostEqual(contract.commission_rate or 0, 0.15)
        self.assertIn(contract.confidence, {"medium", "high"})

    def test_commission_language_without_spend(self) -> None:
        stage_one = "RFP uses an 85/15 commission model on media placements."
        contract = build_pricing_contract(stage_one_text=stage_one, rfp_text="Creative services RFP.")
        self.assertIn(contract.fee_model, {"commission", "hybrid"})
        self.assertIsNone(contract.media_spend_annual)
        self.assertIsNotNone(contract.commission_rate)

    def test_non_commission_hourly(self) -> None:
        stage_one = "Pricing is hourly labor categories only. No media commission."
        contract = build_pricing_contract(
            stage_one_text=stage_one,
            rfp_text="Provide fully burdened hourly rates for each labor category.",
        )
        self.assertNotEqual(contract.fee_model, "commission")
        self.assertIsNone(contract.media_spend_annual)


class CommissionSanitizerTests(unittest.TestCase):
    def _orphan_budget(self) -> ProposalBudget:
        return ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            commissionModel="85/15 traditional media",
            commissionRate=0.15,
            clientMediaPassthrough=None,
            agencyRevenueEstimate=7000.0,
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Media",
                    description="Agency commission on media placements",
                    lineItemType="agency_fee",
                    rate=7000.0,
                    extended=7000.0,
                    unit="flat",
                )
            ],
        )

    def test_orphan_commission_cleared_to_manual_fill_no_invented_base(self) -> None:
        contract = build_pricing_contract(
            stage_one_text="85/15 commission model applies.",
            rfp_text="Media services.",
        )
        self.assertIsNone(contract.media_spend_annual)

        sanitized = sanitize_commission_budget(self._orphan_budget(), contract)
        orphans = collect_orphan_commission_violations(sanitized)
        self.assertEqual(orphans, [], msg=orphans)

        commission_items = [
            item
            for item in sanitized.line_items
            if "commission" in (item.description or "").casefold()
        ]
        self.assertTrue(commission_items)
        for item in commission_items:
            self.assertTrue(item.is_manual_fill)
            self.assertTrue(
                float(item.extended or 0) <= 0.01,
                msg=f"invented commission amount survived: {item.extended}",
            )
            self.assertTrue(extract_manual_fill_tags(item.description or "") or extract_manual_fill_tags(item.notes or ""))

        self.assertTrue(
            float(sanitized.client_media_passthrough or 0) <= 0.01,
            msg="must not invent media base",
        )
        self.assertTrue(
            any("media spend unevidenced" in f.casefold() for f in sanitized.pricing_flags),
            msg=sanitized.pricing_flags,
        )

        # Editor must not hard-halt after sanitize.
        finalized = run_budget_editor_pass(sanitized, rfp_context="Media services.")
        self.assertEqual(collect_orphan_commission_violations(finalized), [])

    def test_orphan_with_lump_sum_does_not_resurrect_revenue(self) -> None:
        contract = build_pricing_contract(
            stage_one_text="85/15 commission model applies.",
            rfp_text="Media services.",
        )
        budget = self._orphan_budget().model_copy(update={"lump_sum_total": 7000.0})
        sanitized = sanitize_commission_budget(budget, contract)
        self.assertTrue(float(sanitized.agency_revenue_estimate or 0) <= 0.01)
        self.assertTrue(float(sanitized.lump_sum_total or 0) <= 0.01)
        finalized = run_budget_editor_pass(sanitized, rfp_context="Media services.")
        self.assertTrue(
            float(finalized.agency_revenue_estimate or 0) <= 0.01,
            msg=f"revenue resurrected: {finalized.agency_revenue_estimate}",
        )

    def test_mixed_labor_and_orphan_commission_survives_editor(self) -> None:
        from app.services.pricing_rate_binding import bind_budget_line_items_to_rate_card
        from app.services.pricing_rate_card_builder import build_pricing_rate_card_from_guide_text

        contract = build_pricing_contract(
            stage_one_text="85/15 commission model applies.",
            rfp_text="Media services.",
        )
        budget = self._orphan_budget().model_copy(
            update={
                "line_items": [
                    *self._orphan_budget().line_items,
                    BudgetLineItem(
                        id="L2",
                        category="Strategy",
                        description="Monthly Social Media Management",
                        rateSource="5.3 — 00_Guide_Pricing Average",
                        rate=4000.0,
                        extended=4000.0,
                        unit="flat",
                    ),
                ],
                "agency_revenue_estimate": 11000.0,
                "lump_sum_total": 11000.0,
            }
        )
        guide = """
        - 5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)
        """
        card = build_pricing_rate_card_from_guide_text(guide)
        sanitized = sanitize_commission_budget(budget, contract)
        bound = bind_budget_line_items_to_rate_card(sanitized, card)
        finalized = run_budget_editor_pass(bound, rfp_context="Media services.")
        self.assertEqual(collect_orphan_commission_violations(finalized), [])
        labor = next(item for item in finalized.line_items if item.id == "L2")
        self.assertAlmostEqual(float(labor.extended or 0), 4000.0)

    def test_non_commission_notes_do_not_drop_labor_line(self) -> None:
        contract = build_pricing_contract(
            stage_one_text="Hourly labor categories only. No media commission.",
            rfp_text="Provide hourly rates.",
        )
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Strategy",
                    description="Discovery workshop series",
                    notes="No media commission on this phase",
                    extended=5000.0,
                    rate=5000.0,
                    unit="flat",
                    isManualFill=True,
                )
            ],
            agencyRevenueEstimate=5000.0,
        )
        sanitized = sanitize_commission_budget(budget, contract)
        self.assertEqual(len(sanitized.line_items), 1)
        self.assertAlmostEqual(float(sanitized.line_items[0].extended or 0), 5000.0)

    def test_evidenced_base_recomputes_fee(self) -> None:
        contract = build_pricing_contract(
            stage_one_text=(
                "85/15 commission. Estimated annual media: $100,000. Commission rate 15%."
            ),
            rfp_text="Annual media budget of $100,000.",
        )
        self.assertEqual(contract.media_spend_annual, 100000.0)

        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            commissionModel="85/15",
            commissionRate=0.15,
            clientMediaPassthrough=None,
            agencyRevenueEstimate=1.0,
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    category="Media",
                    description="Agency commission",
                    lineItemType="agency_fee",
                    extended=1.0,
                    unit="flat",
                )
            ],
        )
        sanitized = sanitize_commission_budget(budget, contract)
        self.assertEqual(sanitized.client_media_passthrough, 100000.0)
        fee_items = [
            item
            for item in sanitized.line_items
            if infer_agency_commission(item)
        ]
        self.assertTrue(fee_items)
        self.assertAlmostEqual(float(fee_items[0].extended or 0), 15000.0)
        pass_items = [
            item
            for item in sanitized.line_items
            if (item.line_item_type or "") == "client_passthrough"
            or "pass-through" in (item.description or "").casefold()
            or "passthrough" in (item.description or "").casefold()
        ]
        self.assertTrue(pass_items)
        self.assertAlmostEqual(float(pass_items[0].extended or 0), 100000.0)

    def test_non_commission_drops_fabricated_commission_fee(self) -> None:
        contract = build_pricing_contract(
            stage_one_text="Hourly labor categories only. No media commission.",
            rfp_text="Provide hourly rates.",
        )
        budget = self._orphan_budget()
        sanitized = sanitize_commission_budget(budget, contract)
        commission_fees = [
            item
            for item in sanitized.line_items
            if "commission" in (item.description or "").casefold()
            and float(item.extended or 0) > 0
        ]
        self.assertEqual(commission_fees, [])


def infer_agency_commission(item: BudgetLineItem) -> bool:
    blob = f"{item.description or ''} {item.notes or ''}".casefold()
    return "commission" in blob and "pass" not in blob


if __name__ == "__main__":
    unittest.main()
