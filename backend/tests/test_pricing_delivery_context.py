"""Shared PricingInstrument + DeliveryConstraints agent context block."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.models.delivery_constraints import (
    DeliveryConstraints,
    DeliveryHorizon,
    DeliveryTrack,
)
from app.models.pricing_instrument import PricingInstrument
from app.models.proposal import ProposalResearchCache
from app.services.pricing_delivery_context import format_pricing_delivery_constraints_block

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dupage_pricing_instrument.json"


def _research_like() -> ProposalResearchCache:
    inst = PricingInstrument.model_validate(json.loads(FIXTURE.read_text()))
    dc = DeliveryConstraints(
        tracks=[
            DeliveryTrack(
                id="part-1",
                label="General Marketing",
                nteAnnual=75000,
                billing="hourly",
            ),
            DeliveryTrack(
                id="part-2",
                label="Young Adult Outreach",
                nteAnnual=100000,
                billing="hourly",
            ),
        ],
        mandatoryDeliverables=[
            "General Marketing communications",
            "Young Adult Outreach Campaign",
        ],
        outOfScope=["County-wide website redesign"],
        nonCommingleTracks=True,
        buyerOwnsDeliverables=True,
        letterProposalGate=True,
        horizon=DeliveryHorizon(
            baseTerm="1 year",
            renewals="up to 3",
            maxTerm="4 years",
        ),
    )
    return ProposalResearchCache(
        rfpId="rfp-t",
        updatedAt="2026-09-23T00:00:00Z",
        pricingInstrument=inst.model_dump(by_alias=True),
        deliveryConstraints=dc.model_dump(by_alias=True),
    )


class PricingDeliveryContextTests(unittest.TestCase):
    def test_block_lists_tracks_and_cost_frozen_rule(self) -> None:
        block = format_pricing_delivery_constraints_block(_research_like())
        self.assertIn("buyer_pricing_form", block)
        self.assertIn("75,000", block)
        self.assertIn("deterministic render only", block.casefold())
        self.assertIn("must not change locked facts", block.casefold())
        self.assertIn("buyer owns final deliverables", block.casefold())
        self.assertIn("letter proposal", block.casefold())

    def test_budget_focus_keeps_tracks_omits_long_sow_list(self) -> None:
        block = format_pricing_delivery_constraints_block(
            _research_like(), focus="budget"
        )
        self.assertIn("buyer_pricing_form", block)
        self.assertIn("75,000", block)
        self.assertIn("deterministic render only", block.casefold())
        # Horizon still useful for budget narrate; mandatory list is sow-heavy.
        self.assertNotIn("County-wide website redesign", block)
        # Ownership/gate still apply when present (short binding facts).
        self.assertIn("buyer owns final deliverables", block.casefold())
        self.assertIn("letter proposal", block.casefold())

    def test_falls_back_to_opportunity_hard_constraints(self) -> None:
        plan = {
            "opportunity": {
                "understanding": {
                    "client": "Acme Corp",
                    "budgetIntel": {"ceiling": "$50,000 NTE"},
                },
                "scope": {"mandatory": ["Deliver website refresh"]},
                "compliance": {"items": []},
            },
            "delivery": {"budget": {"pricingModel": "FFP", "ceiling": "$50,000"}},
        }
        block = format_pricing_delivery_constraints_block(plan, focus="budget")
        self.assertIn("OPPORTUNITY HARD CONSTRAINTS", block)
        self.assertIn("$50,000", block)


if __name__ == "__main__":
    unittest.main()
