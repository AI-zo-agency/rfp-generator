"""Pinned Labor Cost (title) + filename fallback for role billable hourlies."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.rfp import RfpRecord
from app.services import proposal_pricing_service as pps
from app.services.pricing_approved_rates import approved_from_role_billable_text


LABOR_BODY = """\
# Labor Cost

| Role | Billable Rate | Internal Rate |
| --- | --- | --- |
| Account Manager | $145/hr | $90/hr |
| Creative Director | $185/hr | $110/hr |
| Programming | $165/hr | $100/hr |
"""

GUIDE_MENU = """\
**5.1 Monthly Digital Retainer**
| **High** | $8,000 to $12,000 | x |
"""


def _rfp(**kwargs: str) -> RfpRecord:
    base = {
        "id": "r1",
        "title": "SOW",
        "client": "DPHHS",
        "dueDate": "2026-12-01",
        "receivedDate": "2026-01-01",
        "lastActivity": "2026-01-01T00:00:00Z",
        "lastActivityNote": "t",
    }
    base.update(kwargs)
    return RfpRecord(**base)  # type: ignore[arg-type]


class LaborCostPinFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_pins_by_title_and_injects_block(self) -> None:
        fake_doc = {
            "id": "mem-labor",
            "title": "Labor Cost",
            "metadata": {"title": "Labor Cost", "category": "pricing"},
            "customId": "drive:labor",
        }
        with (
            mock.patch.object(pps.supermemory, "is_configured", return_value=True),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_file_name",
                new=mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_title",
                new=mock.AsyncMock(return_value=fake_doc),
            ) as title_mock,
            mock.patch.object(
                pps.supermemory,
                "document_fetch_key",
                return_value="drive:labor",
            ),
            mock.patch.object(
                pps.supermemory,
                "get_document_content",
                new=mock.AsyncMock(return_value=LABOR_BODY),
            ),
            mock.patch.object(
                pps,
                "search_knowledge_base",
                new=mock.AsyncMock(return_value=(GUIDE_MENU, ["00_Guide_Pricing.docx"])),
            ),
            mock.patch.object(
                pps,
                "_fetch_labor_role_rate_context",
                new=mock.AsyncMock(return_value=("", [])),
            ),
        ):
            text, sources = await pps._fetch_guide_context(_rfp(), "")
        self.assertIn("=== LABOR COST (pinned role billable card) ===", text)
        self.assertIn("Account Manager", text)
        self.assertIn("Labor Cost", sources)
        title_mock.assert_awaited()

    async def test_filename_fallback_when_title_misses(self) -> None:
        fake_doc = {
            "id": "mem-fn",
            "metadata": {
                "fileName": "Agency Role Rates & Cost Table.docx",
                "category": "pricing",
            },
            "customId": "drive:fn",
        }

        async def _find_file(name: str):
            if "Agency Role Rates" in name:
                return fake_doc
            return None

        with (
            mock.patch.object(pps.supermemory, "is_configured", return_value=True),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_title",
                new=mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_file_name",
                new=mock.AsyncMock(side_effect=_find_file),
            ),
            mock.patch.object(
                pps.supermemory,
                "document_fetch_key",
                return_value="drive:fn",
            ),
            mock.patch.object(
                pps.supermemory,
                "get_document_content",
                new=mock.AsyncMock(return_value=LABOR_BODY),
            ),
        ):
            pinned = await pps._fetch_pinned_labor_rate_card()
        self.assertIsNotNone(pinned)
        assert pinned is not None
        text, srcs = pinned
        self.assertIn("Creative Director", text)
        self.assertTrue(any("Agency Role Rates" in s for s in srcs))

    async def test_billable_parse_ignores_internal_and_guide_menu(self) -> None:
        rates = approved_from_role_billable_text(
            LABOR_BODY, source_file="Labor Cost"
        )
        amounts = {float(r.amount) for r in rates}
        self.assertIn(145.0, amounts)
        self.assertIn(185.0, amounts)
        self.assertNotIn(90.0, amounts)
        menu_rates = approved_from_role_billable_text(
            GUIDE_MENU, source_file="00_Guide_Pricing.docx"
        )
        self.assertEqual(menu_rates, [])


class DisplayClientNameTests(unittest.TestCase):
    def test_prefers_longer_memory_client_name(self) -> None:
        from app.services.proposal_drafting_graph import resolve_display_client_name

        plan = {
            "proposalMemory": {
                "facts": {
                    "clientName": (
                        "Montana Department of Public Health and Human Services"
                    )
                }
            }
        }
        self.assertEqual(
            resolve_display_client_name("DPHHS", plan),
            "Montana Department of Public Health and Human Services",
        )

    def test_keeps_rfp_client_when_memory_shorter(self) -> None:
        from app.services.proposal_drafting_graph import resolve_display_client_name

        plan = {"proposalMemory": {"facts": {"clientName": "MT"}}}
        self.assertEqual(resolve_display_client_name("DPHHS 988", plan), "DPHHS 988")


class LaborCostProvenanceTests(unittest.TestCase):
    def test_rate_card_stamps_labor_pin_not_guide(self) -> None:
        from app.services.pricing_rate_card_builder import (
            bindable_rates,
            build_pricing_rate_card_from_guide_text,
        )

        bundle = (
            "**5.1 Monthly Digital**\n"
            "| **High** | $8,000 to $12,000 | x |\n\n"
            "=== LABOR COST (pinned role billable card) ===\n"
            "| Role | Billable Rate (USD) | Internal Rate |\n"
            "| --- | --- | --- |\n"
            "| Account Manager | $275.00 | $85.00 |\n"
            "| Creative Director | $275.00 | $85.00 |\n"
        )
        card = build_pricing_rate_card_from_guide_text(bundle)
        hourly = [r for r in bindable_rates(card) if r.unit == "hour"]
        self.assertTrue(hourly)
        for r in hourly:
            self.assertEqual(r.source_doc, "Labor Cost", msg=r.service)

    def test_ingest_prefers_labor_cost_pin_marker(self) -> None:
        from app.services.pricing_approved_rates import (
            ingest_role_billable_from_guide_bundle,
        )

        bundle = (
            "=== 00_Guide_Pricing ===\njunk\n\n"
            "=== LABOR COST (pinned role billable card) ===\n"
            "| Role | Billable Rate (USD) | Internal |\n"
            "| Account Manager | $275.00 | $85.00 |\n"
            "| Agency Director | $400.00 | $150.00 |\n\n"
            "=== KB labor / role billable rates (search) ===\n"
            "| Role | Billable |\n"
            "| Programming | $999.00 |\n"
        )
        rates = ingest_role_billable_from_guide_bundle(
            bundle,
            kb_sources=["00_Guide_Pricing.docx", "Labor Cost"],
        )
        amounts = {float(r.amount) for r in rates}
        self.assertIn(275.0, amounts)
        self.assertIn(400.0, amounts)
        self.assertNotIn(999.0, amounts)
        self.assertTrue(all(r.source_file == "Labor Cost" for r in rates))
