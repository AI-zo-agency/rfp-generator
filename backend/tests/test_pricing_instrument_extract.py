"""Extractor normalization — fixture-driven, no DuPage hardcode in production module."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.services.pricing_instrument_extract import (
    budget_format_for_instrument,
    normalize_delivery_payload,
    normalize_instrument_payload,
)


class ExtractNormalizeTests(unittest.TestCase):
    def test_normalize_buyer_form_payload(self) -> None:
        raw = {
            "kind": "buyer_pricing_form",
            "bidNumber": "26-088-WIOA",
            "identityFields": [
                {"key": "company_name", "label": "COMPANY NAME", "valueSource": "extract"}
            ],
            "tracks": [
                {
                    "id": "part-1",
                    "label": "Part 1",
                    "nteAnnual": 75000,
                    "asksHourly": True,
                    "asksHours": False,
                },
                {
                    "id": "part-2",
                    "label": "Part 2",
                    "nteAnnual": 100000,
                    "asksHourly": True,
                    "asksHours": True,
                },
            ],
            "signature": {
                "printedNameRequired": True,
                "titleRequired": True,
                "signatureRequired": True,
                "dateRequired": True,
            },
            "confidence": 0.85,
            "evidence": [
                {
                    "field": "bid_number",
                    "quote": "BID NUMBER 26-088-WIOA",
                    "locator": "p.27",
                }
            ],
        }
        inst = normalize_instrument_payload(raw)
        self.assertEqual(inst.kind, "buyer_pricing_form")
        self.assertEqual(budget_format_for_instrument(inst), "blended_rate_form")

    def test_low_confidence_none_maps_to_phased(self) -> None:
        inst = normalize_instrument_payload({"kind": "none", "confidence": 0.1})
        self.assertEqual(budget_format_for_instrument(inst), "phased")

    def test_high_confidence_none_still_maps_to_phased(self) -> None:
        """Phase 3.5 align must skip kind=none; format helper still returns phased."""
        inst = normalize_instrument_payload({"kind": "none", "confidence": 0.9})
        self.assertEqual(inst.kind, "none")
        self.assertEqual(budget_format_for_instrument(inst), "phased")

    def test_drops_tracks_without_labels(self) -> None:
        inst = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.7,
                "tracks": [
                    {"id": "ok", "label": "Part 1", "nteAnnual": 10},
                    {"id": "bad", "label": "  ", "nteAnnual": 20},
                    {"id": "also", "label": "", "nteAnnual": 30},
                ],
            }
        )
        self.assertEqual(len(inst.tracks), 1)
        self.assertEqual(inst.tracks[0].label, "Part 1")

    def test_never_invents_bid_number(self) -> None:
        inst = normalize_instrument_payload(
            {"kind": "buyer_pricing_form", "confidence": 0.8, "bidNumber": "   "}
        )
        self.assertIsNone(inst.bid_number)

    def test_clamps_unknown_kind(self) -> None:
        inst = normalize_instrument_payload({"kind": "mystery_form", "confidence": 0.9})
        self.assertEqual(inst.kind, "none")

    def test_normalize_delivery_payload(self) -> None:
        dc = normalize_delivery_payload(
            {
                "tracks": [
                    {"id": "t1", "label": "Track A", "nteAnnual": 50000, "billing": "hourly"}
                ],
                "mandatoryDeliverables": ["Website refresh"],
                "nonCommingleTracks": True,
                "horizon": {"baseTerm": "1 year", "renewals": "up to 2", "maxTerm": "3 years"},
            }
        )
        self.assertEqual(len(dc.tracks), 1)
        self.assertTrue(dc.non_commingle_tracks)
        self.assertEqual(dc.horizon.base_term, "1 year")
        self.assertEqual(dc.mandatory_deliverables, ["Website refresh"])

    def test_personnel_maps_to_personnel_loading(self) -> None:
        inst = normalize_instrument_payload(
            {"kind": "personnel_loading", "confidence": 0.8}
        )
        self.assertEqual(budget_format_for_instrument(inst), "personnel_loading")

    def test_phased_fee_schedule_maps_to_phased(self) -> None:
        inst = normalize_instrument_payload(
            {"kind": "phased_fee_schedule", "confidence": 0.8}
        )
        self.assertEqual(budget_format_for_instrument(inst), "phased")

    def test_opportunity_dual_nte_bootstrap(self) -> None:
        from app.services.pricing_instrument_extract import (
            bootstrap_delivery_tracks_from_opportunity,
        )

        opp = {
            "understanding": {
                "budgetIntel": {
                    "ceiling": "Part 1 NTE $75,000; Part 2 NTE $100,000",
                    "notes": "separate track caps — do not commingle",
                }
            }
        }
        tracks = bootstrap_delivery_tracks_from_opportunity(opp)
        self.assertGreaterEqual(len(tracks), 2)
        amounts = {t.nte_annual for t in tracks}
        self.assertIn(75000.0, amounts)
        self.assertIn(100000.0, amounts)

    def test_bootstrap_skips_unlabeled_dollar_pair(self) -> None:
        from app.services.pricing_instrument_extract import (
            bootstrap_delivery_tracks_from_opportunity,
        )

        opp = {
            "understanding": {
                "budgetIntel": {
                    "ceiling": "Total project budget $500,000; contingency $50,000",
                    "notes": "overall envelope",
                }
            }
        }
        tracks = bootstrap_delivery_tracks_from_opportunity(opp)
        self.assertEqual(tracks, [])

    def test_grounding_drops_unquoted_bid(self) -> None:
        from app.services.pricing_instrument_extract import (
            ground_instrument_against_excerpt,
            normalize_instrument_payload,
        )

        inst = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "bidNumber": "99-FAKE-BID",
                "evidence": [
                    {
                        "field": "bid_number",
                        "quote": "BID NUMBER 99-FAKE-BID",
                        "locator": "p.1",
                    }
                ],
            }
        )
        grounded = ground_instrument_against_excerpt(
            inst, "PRICING FORM\nCOMPANY NAME: ____\nNo bid number here."
        )
        self.assertIsNone(grounded.bid_number)

    def test_grounding_keeps_companyfacts_identity_and_opp_nte(self) -> None:
        from app.services.pricing_instrument_extract import (
            ensure_buyer_form_completeness,
            ground_instrument_against_excerpt,
            merge_delivery_ntes_onto_instrument,
            normalize_delivery_payload,
            normalize_instrument_payload,
        )

        inst = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "bidNumber": "26-088-WIOA",
                "identityFields": [
                    {
                        "key": "company_name",
                        "label": "COMPANY NAME",
                        "valueSource": "companyfacts",
                    }
                ],
                "tracks": [
                    {
                        "id": "part-1",
                        "label": "Part 1",
                        "nteAnnual": 75000,
                        "asksHourly": True,
                        "asksHours": False,
                    },
                    {
                        "id": "part-2",
                        "label": "Part 2",
                        "nteAnnual": 100000,
                        "asksHourly": True,
                        "asksHours": True,
                    },
                ],
            }
        )
        # Form excerpt has labels + bid but not the dollar amounts.
        excerpt = (
            "PROPOSAL PRICING FORM\nBID NUMBER 26-088-WIOA\n"
            "COMPANY NAME\nCONTACT PERSON\nCONTACT EMAIL\n"
            "Printed Name Signature Title Date\n"
        )
        opp = "General Marketing track: $75,000 annual NTE; Youth Campaign: $100,000 NTE"
        grounded = ground_instrument_against_excerpt(
            inst, excerpt, secondary_corpus=opp
        )
        self.assertEqual(grounded.bid_number, "26-088-WIOA")
        self.assertEqual(len(grounded.identity_fields), 1)
        self.assertEqual(grounded.identity_fields[0].value_source, "companyfacts")
        self.assertEqual(grounded.tracks[0].nte_annual, 75000.0)
        self.assertEqual(grounded.tracks[1].nte_annual, 100000.0)

        complete = ensure_buyer_form_completeness(grounded, excerpt)
        keys = {f.key for f in complete.identity_fields}
        self.assertIn("contact_person", keys)
        self.assertIn("contact_email", keys)
        self.assertIsNotNone(complete.signature)
        self.assertTrue(complete.signature.signature_required)

        # Merge path: delivery has NTEs, instrument tracks nulled.
        thin = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "tracks": [
                    {"id": "part-1", "label": "Part 1", "asksHourly": True},
                    {"id": "part-2", "label": "Part 2", "asksHourly": True, "asksHours": True},
                ],
            }
        )
        delivery = normalize_delivery_payload(
            {
                "tracks": [
                    {"id": "part-1", "label": "Part 1", "nteAnnual": 75000},
                    {"id": "part-2", "label": "Part 2", "nteAnnual": 100000},
                ]
            }
        )
        merged = merge_delivery_ntes_onto_instrument(thin, delivery)
        self.assertEqual(merged.tracks[0].nte_annual, 75000.0)
        self.assertEqual(merged.tracks[1].nte_annual, 100000.0)

    def test_instrument_needs_refresh_when_incomplete(self) -> None:
        from app.services.pricing_instrument_extract import (
            instrument_needs_refresh,
            normalize_instrument_payload,
        )

        self.assertTrue(instrument_needs_refresh(None))
        incomplete = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "tracks": [
                    {"id": "a", "label": "A", "asksHourly": True},
                    {"id": "b", "label": "B", "asksHourly": True},
                ],
            }
        )
        self.assertTrue(instrument_needs_refresh(incomplete))
        complete = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "identityFields": [
                    {
                        "key": "company_name",
                        "label": "COMPANY NAME",
                        "valueSource": "companyfacts",
                    },
                    {
                        "key": "contact_person",
                        "label": "CONTACT PERSON",
                        "valueSource": "companyfacts",
                    },
                    {
                        "key": "contact_email",
                        "label": "CONTACT EMAIL",
                        "valueSource": "companyfacts",
                    },
                ],
                "tracks": [
                    {"id": "a", "label": "A", "nteAnnual": 1, "asksHourly": True},
                    {"id": "b", "label": "B", "nteAnnual": 2, "asksHourly": True},
                ],
            }
        )
        self.assertFalse(instrument_needs_refresh(complete))

    def test_low_confidence_buyer_form_demoted(self) -> None:
        from app.services.pricing_instrument_extract import (
            apply_buyer_form_confidence_gate,
            normalize_instrument_payload,
        )

        inst = normalize_instrument_payload(
            {"kind": "buyer_pricing_form", "confidence": 0.4}
        )
        demoted = apply_buyer_form_confidence_gate(inst)
        self.assertEqual(demoted.kind, "none")

    def test_low_confidence_structural_rescue(self) -> None:
        from app.services.pricing_instrument_extract import (
            apply_buyer_form_confidence_gate,
            ensure_buyer_form_completeness,
            normalize_instrument_payload,
        )

        inst = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.4,
                "bidNumber": "26-088-WIOA",
                "tracks": [
                    {
                        "id": "part-1",
                        "label": "Part 1",
                        "nteAnnual": 75000,
                        "asksHourly": True,
                    },
                    {
                        "id": "part-2",
                        "label": "Part 2",
                        "nteAnnual": 100000,
                        "asksHourly": True,
                        "asksHours": True,
                    },
                ],
            }
        )
        rescued = apply_buyer_form_confidence_gate(inst)
        self.assertEqual(rescued.kind, "buyer_pricing_form")
        self.assertGreaterEqual(rescued.confidence, 0.55)
        complete = ensure_buyer_form_completeness(rescued, excerpt="")
        keys = {f.key for f in complete.identity_fields}
        self.assertEqual(keys, {"company_name", "contact_person", "contact_email"})
        self.assertIsNotNone(complete.signature)

    def test_ensure_completes_partial_identity_on_structural_form(self) -> None:
        from app.services.pricing_instrument_extract import (
            ensure_buyer_form_completeness,
            instrument_needs_refresh,
            normalize_instrument_payload,
        )

        partial = normalize_instrument_payload(
            {
                "kind": "buyer_pricing_form",
                "confidence": 0.9,
                "bidNumber": "26-088-WIOA",
                "identityFields": [
                    {
                        "key": "company_name",
                        "label": "COMPANY NAME",
                        "valueSource": "companyfacts",
                    },
                    {
                        "key": "contact_person",
                        "label": "CONTACT PERSON",
                        "valueSource": "companyfacts",
                    },
                ],
                "tracks": [
                    {
                        "id": "part-1",
                        "label": "Part 1",
                        "nteAnnual": 75000,
                        "asksHourly": True,
                    },
                    {
                        "id": "part-2",
                        "label": "Part 2",
                        "nteAnnual": 100000,
                        "asksHourly": True,
                        "asksHours": True,
                    },
                ],
            }
        )
        self.assertTrue(instrument_needs_refresh(partial))
        complete = ensure_buyer_form_completeness(partial, excerpt="")
        keys = {f.key for f in complete.identity_fields}
        self.assertEqual(keys, {"company_name", "contact_person", "contact_email"})
        self.assertFalse(instrument_needs_refresh(complete))


class ExtractAsyncSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mocked_llm_buyer_form(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.services.pricing_instrument_extract import (
            extract_pricing_and_delivery_constraints,
        )

        fixture = (
            Path(__file__).parent / "fixtures" / "dupage_pricing_form_excerpt.txt"
        ).read_text()
        llm_json = {
            "instrument": {
                "kind": "buyer_pricing_form",
                "bidNumber": "26-088-WIOA",
                "identityFields": [
                    {
                        "key": "company_name",
                        "label": "COMPANY NAME",
                        "valueSource": "extract",
                    }
                ],
                "tracks": [
                    {
                        "id": "part-1",
                        "label": "Part 1 — General Marketing",
                        "nteAnnual": 75000,
                        "asksHourly": True,
                        "asksHours": False,
                    },
                    {
                        "id": "part-2",
                        "label": "Part 2 — Young Adult Outreach",
                        "nteAnnual": 100000,
                        "asksHourly": True,
                        "asksHours": True,
                    },
                ],
                "signature": {
                    "printedNameRequired": True,
                    "titleRequired": True,
                    "signatureRequired": True,
                    "dateRequired": True,
                },
                "confidence": 0.9,
                "evidence": [
                    {
                        "field": "bid_number",
                        "quote": "BID NUMBER: 26-088-WIOA",
                        "locator": "excerpt",
                    }
                ],
            },
            "delivery": {
                "tracks": [
                    {
                        "id": "part-1",
                        "label": "Part 1 — General Marketing",
                        "nteAnnual": 75000,
                        "billing": "hourly",
                    }
                ],
                "nonCommingleTracks": True,
            },
        }
        with patch(
            "app.services.pricing_instrument_extract.safe_chat_json",
            new=AsyncMock(return_value=(llm_json, "mock")),
        ):
            inst, delivery = await extract_pricing_and_delivery_constraints(
                fixture, None
            )
        self.assertEqual(inst.kind, "buyer_pricing_form")
        self.assertEqual(inst.bid_number, "26-088-WIOA")
        self.assertEqual(len(inst.tracks), 2)
        self.assertTrue(delivery.non_commingle_tracks)

    async def test_llm_failure_returns_none(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.services.pricing_instrument_extract import (
            extract_pricing_and_delivery_constraints,
        )

        with patch(
            "app.services.pricing_instrument_extract.safe_chat_json",
            new=AsyncMock(return_value=({}, "none")),
        ):
            inst, delivery = await extract_pricing_and_delivery_constraints(
                "Some RFP with a pricing form here.", None
            )
        self.assertEqual(inst.kind, "none")
        self.assertEqual(inst.confidence, 0.0)
        self.assertEqual(delivery.tracks, [])


if __name__ == "__main__":
    unittest.main()
