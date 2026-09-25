"""Line-item payload extraction from Stage 3 budget JSON."""

from __future__ import annotations

import unittest

from app.services import proposal_pricing_service as pps


class LineItemsPayloadExtractionTests(unittest.TestCase):
    def test_accepts_snake_case_key(self) -> None:
        raw = {
            "line_items": [
                {
                    "id": "li-1",
                    "description": "Discovery",
                    "category": "labor",
                    "lineItemType": "agency_fee",
                    "quantity": 1,
                    "rate": 1000,
                    "extended": 1000,
                }
            ]
        }
        items = pps._parse_line_items_from_raw(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].description, "Discovery")

    def test_flattens_phased_nested_line_items(self) -> None:
        raw = {
            "phases": [
                {
                    "name": "Discovery",
                    "lineItems": [
                        {
                            "description": "Kickoff & research",
                            "rate": 5000,
                            "extended": 5000,
                            "quantity": 1,
                        }
                    ],
                },
                {
                    "name": "Strategy",
                    "items": [
                        {
                            "name": "Brand platform",
                            "hourlyRate": 2500,
                            "subtotal": 2500,
                        }
                    ],
                },
            ]
        }
        items = pps._parse_line_items_from_raw(raw)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].description, "Kickoff & research")
        self.assertEqual(items[1].description, "Brand platform")

