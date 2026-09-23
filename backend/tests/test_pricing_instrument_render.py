"""Buyer pricing form render from PricingInstrument (DuPage golden)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.models.pricing_instrument import PricingInstrument
from app.services.pricing_instrument_render import render_pricing_instrument_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "dupage_pricing_instrument.json"


class InstrumentRenderTests(unittest.TestCase):
    def test_dupage_golden_has_bid_parts_signature_no_fein(self) -> None:
        inst = PricingInstrument.model_validate(json.loads(FIXTURE.read_text()))
        md = render_pricing_instrument_markdown(inst)
        self.assertIn("26-088-WIOA", md)
        self.assertIn("COMPANY NAME", md)
        self.assertIn("CONTACT PERSON", md)
        self.assertIn("CONTACT EMAIL", md)
        self.assertIn("connect@zo.agency", md)
        self.assertIn("Part 1", md)
        self.assertIn("75,000", md)
        self.assertIn("Part 2", md)
        self.assertIn("100,000", md)
        self.assertIn("[MANUAL FILL: SONJA]", md)
        self.assertIn("Printed Name", md)
        self.assertIn("[SIGN]", md)
        self.assertNotIn("FEIN", md)
        self.assertNotIn("Federal Tax", md)
        self.assertNotIn("Fax", md)
        self.assertNotIn("by role", md.casefold())

    def test_phased_kind_returns_empty_so_caller_uses_fee_detail(self) -> None:
        inst = PricingInstrument(kind="phased_fee_schedule", confidence=0.8)
        self.assertEqual(render_pricing_instrument_markdown(inst).strip(), "")

    def test_cap_gate_clears_over_nte(self) -> None:
        from app.models.pricing_instrument import PricingTrack
        from app.services.pricing_instrument_render import apply_track_cap_gate

        inst = PricingInstrument(
            kind="buyer_pricing_form",
            tracks=[
                PricingTrack(
                    id="p1",
                    label="Part 1",
                    nteAnnual=75000,
                    asksHourly=True,
                    asksHours=True,
                    rate=275,
                    hours=400,
                )
            ],
        )
        gated = apply_track_cap_gate(inst)
        self.assertIsNone(gated.tracks[0].hours)

    def test_asks_hours_false_omits_hours_cell(self) -> None:
        from app.models.pricing_instrument import PricingTrack

        inst = PricingInstrument(
            kind="buyer_pricing_form",
            confidence=0.9,
            tracks=[
                PricingTrack(
                    id="p1",
                    label="Part 1 — General Marketing",
                    nteAnnual=75000,
                    asksHourly=True,
                    asksHours=False,
                )
            ],
        )
        md = render_pricing_instrument_markdown(inst)
        self.assertIn("Hourly rate:", md)
        self.assertIn("[MANUAL FILL: SONJA]", md)
        self.assertNotIn("×", md)
        self.assertNotIn("[hours]", md.casefold())

    def test_low_confidence_buyer_form_renders_empty(self) -> None:
        inst = PricingInstrument(kind="buyer_pricing_form", confidence=0.4)
        self.assertEqual(render_pricing_instrument_markdown(inst).strip(), "")


if __name__ == "__main__":
    unittest.main()
