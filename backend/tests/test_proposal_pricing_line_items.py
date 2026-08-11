"""Fail-closed when Stage 3 budget LLM omits lineItems."""

from __future__ import annotations

import unittest
from unittest import mock

from app.services import proposal_pricing_service as pps
from app.services.proposal_common import ProposalError


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


class MissingLineItemsRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_raises_when_retry_still_has_no_line_items(self) -> None:
        shell = {
            "budgetFormat": "lump_sum",
            "confidence": 70,
            "feeStructure": "hourly",
            "pricingTier": "Average",
            "scopeSummary": "Scope only.",
        }
        llm_mock = mock.AsyncMock(
            side_effect=[
                (shell, "OpenRouter"),
                (shell, "OpenRouter"),
            ]
        )
        with mock.patch.object(pps.llm, "chat_json", llm_mock):
            with mock.patch.object(pps, "load_rfp_for_proposal", return_value=(mock.Mock(title="T", client="C", sector="Gov", location="", estimated_value=None), "", "rfp text")):
                with mock.patch.object(pps, "aget_research_cache", return_value=None):
                    with mock.patch.object(pps, "_stage_one_text", return_value=("stage1", True)):
                        with mock.patch.object(pps, "_structural_map_text", return_value=("stage2", True)):
                            with mock.patch.object(pps, "_fetch_guide_context", return_value=("guide", ["00_Guide_Pricing.docx"])):
                                with mock.patch("app.services.pricing_rate_card_store.build_stable_rate_card", return_value=mock.Mock(rates=[mock.Mock()] * 5, warnings=[])):
                                    with mock.patch.object(pps, "assert_rate_card_usable"):
                                        with mock.patch("app.services.pricing_contract_builder.build_pricing_contract", return_value=mock.Mock()):
                                            with mock.patch("app.services.pricing_contract_builder.format_pricing_contract_for_prompt", return_value=""):
                                                with mock.patch("app.services.commission_budget_sanitizer.sanitize_commission_budget", side_effect=lambda b: b):
                                                    with mock.patch("app.services.pricing_rate_binding.bind_budget_line_items_to_rate_card", side_effect=lambda b, **_: b):
                                                        with mock.patch.object(pps, "asave_research_cache", new=mock.AsyncMock()):
                                                            with self.assertRaises(ProposalError) as ctx:
                                                                await pps.generate_proposal_budget("rfp-test")

        self.assertIn("no lineItems", str(ctx.exception))
        self.assertEqual(llm_mock.await_count, 2)
