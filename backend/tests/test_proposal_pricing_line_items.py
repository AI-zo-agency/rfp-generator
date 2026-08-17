"""Fail-closed when Stage 3 budget LLM omits lineItems."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.pricing_rate_card import PricingRate, PricingRateCard
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


class MissingLineItemsRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_skeleton_when_retry_still_has_no_line_items(self) -> None:
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
        card = PricingRateCard(
            rates=[
                PricingRate(
                    rateId="r-disc",
                    service="Discovery workshop",
                    tier="Average",
                    menuId="1.1",
                    amount=4500,
                    unit="fixed",
                ),
                PricingRate(
                    rateId="r-strat",
                    service="Strategy platform",
                    tier="Average",
                    menuId="2.1",
                    amount=6200,
                    unit="fixed",
                ),
            ]
        )
        contract = mock.Mock()
        contract.model_dump.return_value = {}
        contract.fee_model = "fixed"

        with mock.patch.object(pps.llm, "chat_json", llm_mock):
            with mock.patch.object(
                pps,
                "load_rfp_for_proposal",
                return_value=(
                    mock.Mock(
                        title="T",
                        client="C",
                        sector="Gov",
                        location="",
                        estimated_value=None,
                    ),
                    "",
                    "rfp text",
                ),
            ):
                with mock.patch.object(pps, "aget_research_cache", return_value=None):
                    with mock.patch.object(pps, "_stage_one_text", return_value=("stage1", True)):
                        with mock.patch.object(pps, "_structural_map_text", return_value=("stage2", True)):
                            with mock.patch.object(
                                pps, "_fetch_guide_context", return_value=("guide", ["00_Guide_Pricing.docx"])
                            ):
                                with mock.patch(
                                    "app.services.pricing_rate_card_store.build_stable_rate_card",
                                    return_value=card,
                                ):
                                    with mock.patch.object(pps, "assert_rate_card_usable"):
                                        with mock.patch(
                                            "app.services.pricing_contract_builder.build_pricing_contract",
                                            return_value=contract,
                                        ):
                                            with mock.patch(
                                                "app.services.pricing_contract_builder.format_pricing_contract_for_prompt",
                                                return_value="",
                                            ):
                                                with mock.patch(
                                                    "app.services.commission_budget_sanitizer.sanitize_commission_budget",
                                                    side_effect=lambda b, *_a, **_k: b,
                                                ):
                                                    with mock.patch(
                                                        "app.services.pricing_rate_binding.bind_budget_line_items_to_rate_card",
                                                        side_effect=lambda b, *_a, **_k: b,
                                                    ):
                                                        with mock.patch.object(
                                                            pps,
                                                            "run_budget_editor_pass",
                                                            side_effect=lambda b, **_k: b,
                                                        ):
                                                            with mock.patch.object(
                                                                pps,
                                                                "_run_budget_grounding_audit",
                                                                new=mock.AsyncMock(return_value=([], [])),
                                                            ):
                                                                with mock.patch.object(
                                                                    pps,
                                                                    "generate_fee_justification_memo",
                                                                    new=mock.AsyncMock(return_value=""),
                                                                ):
                                                                        with mock.patch(
                                                                            "app.services.proposal_budget_content.prepare_budget_for_client_display",
                                                                            side_effect=lambda b: b,
                                                                        ):
                                                                            with mock.patch.object(
                                                                                pps,
                                                                                "asave_research_cache",
                                                                                new=mock.AsyncMock(),
                                                                            ):
                                                                                budget, _research = await pps.generate_proposal_budget(
                                                                                    "rfp-test"
                                                                                )

        self.assertGreaterEqual(len(budget.line_items), 2)
        self.assertTrue(
            any("omitted lineItems" in f for f in (budget.pricing_flags or []))
        )
        self.assertEqual(llm_mock.await_count, 2)

    def test_skeleton_uses_guide_rates_not_invented_dollars(self) -> None:
        card = PricingRateCard(
            rates=[
                PricingRate(
                    rateId="r1",
                    service="Discovery workshop",
                    tier="Average",
                    menuId="1.1",
                    amount=4500,
                    unit="fixed",
                )
            ]
        )
        items = pps.skeleton_line_items_from_rate_card(card)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].rate, 4500)
        self.assertEqual(items[0].source_rate_id, "r1")
