"""Approved hourly rates: KB role billable + JSON + gates (edge cases)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services import pricing_approved_rates as mod
from app.services.pricing_approved_rates import (
    ApprovedHourlyRate,
    approved_from_pricing_rates,
    approved_from_role_billable_text,
    ingest_role_billable_from_guide_bundle,
    load_approved_hourly_rates,
    merge_approved_hourly_rates,
    resolve_approved_hourly,
    scrub_unapproved_form_rates,
    source_is_blocked,
)
from app.services.proposal_budget_content import (
    derive_blended_form_rates,
    render_budget_markdown,
    render_buyer_pricing_form_worksheet,
)

# Minimal shape matching KB Agency Role Rates (Billable | Internal | Raw floor).
_ROLE_TABLE = """
| Role | Billable Rate (USD) | Internal Rate with benefits, taxes, and costs | Raw floor cost |
| --- | --- | --- | --- |
| Account Manager | $275.00 | $85.00 | $50.00 |
| Agency Director | $400.00 | $150.00 | $100.00 |
| Art Director | $275.00 | $85.00 | $50.00 |
| Contractor | $275.00 | $85.00 | $50–$100.00 |
| Copywriter | $275.00 | $80.00 | $50.00 |
| Creative Director | $275.00 | $100.00 | $50.00 |
| Digital Team | $275.00 | $100.00 | $30.00 |
| Programming | $400.00 | $200.00 | $150.00 |
| Executive | $275.00 | $125.00 | $100.00 |
| Finance | $275.00 | $125.00 | $100.00 |
"""

_GUIDE_MENU_SNIPPET = """
**4.1 Custom Graphic Design per asset**
| **Average** | $275 to $450 |
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoleBillableIngestTests(unittest.TestCase):
    def test_blocks_07_fin(self) -> None:
        self.assertTrue(source_is_blocked("07_FIN_CityofLakeOswego.pdf"))
        self.assertFalse(source_is_blocked("Agency Role Rates & Cost Table.docx"))

    def test_parses_billable_not_internal_or_floor(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        by_label = {r.label.casefold(): r for r in rates}
        self.assertIn("account manager", by_label)
        self.assertEqual(by_label["account manager"].amount, 275.0)
        self.assertEqual(by_label["agency director"].amount, 400.0)
        self.assertEqual(by_label["programming"].amount, 400.0)
        # Must not pick Internal ($85) or Raw floor ($50) as billable.
        amounts = {r.amount for r in rates}
        self.assertNotIn(85.0, amounts)
        self.assertNotIn(50.0, amounts)
        self.assertNotIn(30.0, amounts)
        self.assertTrue(all(r.approved_by == mod.KB_ROLE_APPROVER for r in rates))

    def test_blocked_source_file_yields_nothing(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="07_FIN_LakeOswego_Proposal.pdf"
        )
        self.assertEqual(rates, [])

    def test_guide_menu_sku_not_promoted_as_hourly(self) -> None:
        card = PricingRateCard(
            rates=[
                PricingRate(
                    rateId="guide-4-1",
                    service="Custom Graphic Design per asset",
                    unit="fixed",
                    amount=275.0,
                    menuId="4.1",
                    sourceDoc="00_Guide_Pricing.docx",
                ),
                PricingRate(
                    rateId="role-hourly-digital-team",
                    service="Digital Team",
                    unit="hour",
                    amount=275.0,
                    menuId="",
                    sourceDoc="Agency Role Rates & Cost Table.docx",
                    notes="billable rate from KB role/labor table (not internal/floor)",
                ),
            ]
        )
        approved = approved_from_pricing_rates(card.rates)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].rate_id, "role-hourly-digital-team")

    def test_resolve_275_from_role_table(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        hit = resolve_approved_hourly(275.0, registry=rates)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.amount, 275.0)

    def test_resolve_400_director(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        hit = resolve_approved_hourly(400.0, registry=rates)
        self.assertIsNotNone(hit)
        self.assertIn(hit.label.casefold(), {"agency director", "programming"})

    def test_07_fin_source_argument_blocks_resolve(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        self.assertIsNone(
            resolve_approved_hourly(
                275.0, source="07_FIN_lost_bid.pdf", registry=rates
            )
        )

    def test_human_json_wins_over_kb_on_rate_id(self) -> None:
        kb = [
            ApprovedHourlyRate(
                rateId="role-hourly-digital-team",
                label="Digital Team",
                amount=275.0,
                approvedBy=mod.KB_ROLE_APPROVER,
                sourceFile="Agency Role Rates.docx",
            )
        ]
        human = [
            ApprovedHourlyRate(
                rateId="role-hourly-digital-team",
                label="Digital Team (Sonja override)",
                amount=250.0,
                approvedBy="Sonja Anderson",
                sourceFile="pricing_approved_hourly_rates.json",
            )
        ]
        merged = merge_approved_hourly_rates(human, kb)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].amount, 250.0)
        self.assertEqual(merged[0].approved_by, "Sonja Anderson")

    def test_scrub_keeps_kb_billable_clears_fin(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=275.0,
            formMonthlyRate=20000.0,
            formAnnualRate=240000.0,
            lineItems=[
                BudgetLineItem(
                    id="ok",
                    category="Part 1",
                    description="Digital",
                    unit="hour",
                    rate=275.0,
                    quantity=10,
                    extended=2750.0,
                    rateSource="Agency Role Rates & Cost Table.docx",
                ),
                BudgetLineItem(
                    id="bad",
                    category="Part 2",
                    description="From lost bid",
                    unit="hour",
                    rate=275.0,
                    quantity=10,
                    extended=2750.0,
                    rateSource="07_FIN_LakeOswego.pdf",
                ),
            ],
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=rates)
        self.assertEqual(cleaned.form_hourly_rate, 275.0)
        self.assertIsNone(cleaned.form_monthly_rate)
        self.assertIsNone(cleaned.form_annual_rate)
        by_id = {i.id: i for i in cleaned.line_items}
        self.assertEqual(by_id["ok"].rate, 275.0)
        self.assertIsNone(by_id["bad"].rate)
        self.assertTrue(by_id["bad"].is_manual_fill)

    def test_scrub_rejects_amount_not_on_role_card(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=199.0,  # not on card
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=rates)
        # Unapproved amount cleared, then seeded from role-card mode ($275).
        self.assertEqual(cleaned.form_hourly_rate, 275.0)
        self.assertTrue(
            any("cleared" in (f or "").casefold() for f in (cleaned.pricing_flags or []))
        )

    def test_phased_guide_fees_untouched(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="phased",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Discovery",
                    description="1.1 Stakeholder Interviews",
                    unit="project",
                    rate=6000.0,
                    quantity=1,
                    extended=6000.0,
                    rateSource="1.1 — Average",
                )
            ],
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=rates)
        self.assertEqual(cleaned.line_items[0].rate, 6000.0)

    def test_ingest_bundle_uses_labor_section_not_guide_sku(self) -> None:
        bundle = (
            "=== 00_Guide_Pricing ===\n"
            + _GUIDE_MENU_SNIPPET
            + "\n\n=== KB labor / role billable rates (search — cite source filenames) ===\n"
            + _ROLE_TABLE
        )
        rates = ingest_role_billable_from_guide_bundle(
            bundle,
            kb_sources=[
                "00_Guide_Pricing.docx",
                "Agency Role Rates & Cost Table.docx",
            ],
        )
        self.assertTrue(any(r.amount == 275.0 for r in rates))
        self.assertTrue(any(r.amount == 400.0 for r in rates))
        # Live rates come from the bundle / rate_card — not a disk KB cache.
        with patch.object(mod, "_load_json_registry", return_value=[]):
            loaded = load_approved_hourly_rates()
        self.assertEqual(loaded, [])
        from app.services.pricing_rate_card_builder import (
            build_pricing_rate_card_from_guide_text,
        )

        card = build_pricing_rate_card_from_guide_text(bundle)
        loaded_card = load_approved_hourly_rates(rate_card=card)
        self.assertTrue(any(r.amount == 275.0 for r in loaded_card))

    def test_guide_only_bundle_does_not_invent_hourly_registry(self) -> None:
        """Menu SKU ranges must never become approved hourlies."""
        bundle = "=== 00_Guide_Pricing ===\n" + _GUIDE_MENU_SNIPPET
        rates = ingest_role_billable_from_guide_bundle(
            bundle, kb_sources=["00_Guide_Pricing.docx"]
        )
        self.assertEqual(rates, [])

    def test_missing_guide_stub_still_empty_rate_card(self) -> None:
        from app.services.pricing_rate_card_builder import (
            build_pricing_rate_card_from_guide_text,
        )

        card = build_pricing_rate_card_from_guide_text("(No 00_Guide_Pricing in KB)")
        self.assertEqual(card.rates, [])

    def test_labor_header_parens_do_not_trip_empty_stub(self) -> None:
        """Regression: '(search — cite…)' after labor marker must still parse."""
        from app.services.pricing_rate_card_builder import (
            build_pricing_rate_card_from_guide_text,
        )

        text = (
            "=== KB labor / role billable rates (search — cite source filenames) ===\n"
            + _ROLE_TABLE
        )
        card = build_pricing_rate_card_from_guide_text(
            text, source_doc="Agency Role Rates & Cost Table.docx"
        )
        amounts = {r.amount for r in card.rates if (r.unit or "") == "hour"}
        self.assertIn(275.0, amounts)
        self.assertIn(400.0, amounts)
        self.assertNotIn(85.0, amounts)
        self.assertNotIn(50.0, amounts)

    def test_render_shows_275_when_registry_has_role_rates(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=275.0,
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=rates)
        with patch.object(mod, "load_approved_hourly_rates", return_value=rates):
            hourly, monthly, annual, _ = derive_blended_form_rates(cleaned)
            self.assertEqual(hourly, 275.0)
            self.assertIsNone(monthly)
            self.assertIsNone(annual)
            md = render_budget_markdown(
                cleaned,
                rfp_text="Submit the Proposal Pricing Form.",
            )
        self.assertIn("$275", md)
        self.assertNotIn("College", md)
        self.assertNotIn("## Terms", md)
        self.assertNotIn("Fee Detail by Phase", md)

    def test_empty_registry_still_manual_fill(self) -> None:
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=275.0,
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=[])
        self.assertIsNone(cleaned.form_hourly_rate)
        md = render_buyer_pricing_form_worksheet(cleaned)
        self.assertIn("MANUAL FILL: SONJA", md)

    def test_null_hourly_seeds_from_role_card_mode(self) -> None:
        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=None,
        )
        cleaned = scrub_unapproved_form_rates(budget, registry=rates)
        self.assertEqual(cleaned.form_hourly_rate, 275.0)

    def test_buyer_pricing_form_does_not_seed_form_hourly(self) -> None:
        from app.models.pricing_instrument import PricingInstrument

        rates = approved_from_role_billable_text(
            _ROLE_TABLE, source_file="Agency Role Rates & Cost Table.docx"
        )
        inst = PricingInstrument(kind="buyer_pricing_form", confidence=0.9)
        budget = ProposalBudget(
            rfpId="rfp-t",
            updatedAt=_now(),
            budgetFormat="blended_rate_form",
            formHourlyRate=275.0,
        )
        cleaned = scrub_unapproved_form_rates(
            budget, registry=rates, pricing_instrument=inst
        )
        self.assertIsNone(cleaned.form_hourly_rate)
        # Null form + buyer instrument must not re-seed.
        again = scrub_unapproved_form_rates(
            cleaned, registry=rates, pricing_instrument=inst
        )
        self.assertIsNone(again.form_hourly_rate)


class ManuscriptHourlyClaimScrubTests(unittest.TestCase):
    def test_range_and_unapproved_single_become_manual_fill(self) -> None:
        from app.services.pricing_approved_rates import (
            scrub_unapproved_manuscript_hourly_claims,
        )

        registry = [
            ApprovedHourlyRate(
                rateId="am",
                amount=145.0,
                label="Account Manager",
                approvedBy="KB",
                sourceFile="Labor Cost",
            )
        ]
        body = (
            "Strategy work runs $150-$250/hr. "
            "Account Manager at $145/hr. "
            "Blended creative at $175/hr."
        )
        out, logs = scrub_unapproved_manuscript_hourly_claims(
            body, registry=registry
        )
        self.assertIn("$145/hr", out)
        self.assertNotIn("$150-$250/hr", out)
        self.assertNotIn("$175/hr", out)
        self.assertIn("MANUAL FILL", out)
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
