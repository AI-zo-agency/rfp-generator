"""PricingInstrument / DeliveryConstraints model contracts."""
from __future__ import annotations

import unittest

from app.models.delivery_constraints import DeliveryConstraints, DeliveryHorizon, DeliveryTrack
from app.models.pricing_instrument import IdentityField, PricingInstrument, PricingTrack
from app.models.proposal import ProposalResearchCache


class PricingInstrumentModelTests(unittest.TestCase):
    def test_buyer_form_round_trip(self) -> None:
        inst = PricingInstrument(
            kind="buyer_pricing_form",
            bidNumber="26-088-WIOA",
            identityFields=[
                IdentityField(key="company_name", label="COMPANY NAME", valueSource="companyfacts"),
                IdentityField(key="contact_person", label="CONTACT PERSON", valueSource="companyfacts"),
                IdentityField(key="contact_email", label="CONTACT EMAIL", valueSource="companyfacts"),
            ],
            tracks=[
                PricingTrack(
                    id="part-1",
                    label="Part 1 — General Marketing",
                    nteAnnual=75000,
                    asksHourly=True,
                    asksHours=False,
                ),
                PricingTrack(
                    id="part-2",
                    label="Part 2 — Young Adult Outreach",
                    nteAnnual=100000,
                    asksHourly=True,
                    asksHours=True,
                ),
            ],
            signature={
                "printedNameRequired": True,
                "titleRequired": True,
                "signatureRequired": True,
                "dateRequired": True,
            },
            confidence=0.9,
            internalNoteRules=["one_hourly_per_track", "do_not_commingle_ntes"],
        )
        raw = inst.model_dump(by_alias=True)
        again = PricingInstrument.model_validate(raw)
        self.assertEqual(again.kind, "buyer_pricing_form")
        self.assertEqual(again.bid_number, "26-088-WIOA")
        self.assertEqual(len(again.tracks), 2)

    def test_research_cache_accepts_instrument(self) -> None:
        dc = DeliveryConstraints(
            tracks=[
                DeliveryTrack(id="part-1", label="General Marketing", nteAnnual=75000, billing="hourly")
            ],
            nonCommingleTracks=True,
            horizon=DeliveryHorizon(baseTerm="1 year", renewals="up to 3", maxTerm="4 years"),
        )
        cache = ProposalResearchCache(
            rfpId="rfp-t",
            updatedAt="2026-09-23T00:00:00Z",
            pricingInstrument={"kind": "none", "confidence": 0.2},
            deliveryConstraints=dc.model_dump(by_alias=True),
        )
        self.assertEqual(cache.pricing_instrument.kind, "none")
        self.assertTrue(cache.delivery_constraints.non_commingle_tracks)


if __name__ == "__main__":
    unittest.main()
