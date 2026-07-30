"""Pinned 00_Guide_Pricing.docx fetch for rate-card builds."""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.rfp import RfpRecord
from app.services import proposal_pricing_service as pps


def _rfp(**kwargs: str) -> RfpRecord:
    base = {
        "id": "r1",
        "title": "Cover Sheet",
        "client": "AHEC",
        "dueDate": "2026-12-01",
        "receivedDate": "2026-01-01",
        "lastActivity": "2026-01-01T00:00:00Z",
        "lastActivityNote": "t",
    }
    base.update(kwargs)
    return RfpRecord(**base)  # type: ignore[arg-type]


class PricingGuidePinFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_guide_context_pins_docx_by_filename(self) -> None:
        fake_doc = {
            "id": "mem1",
            "metadata": {"fileName": "00_Guide_Pricing.docx", "category": "pricing"},
            "customId": "drive:abc",
        }
        body = (
            "**5.3 Monthly Social Media Management (3 Platforms)**\n\n"
            "| **Average** | $3,200 to $4,800 | x |\n"
        )
        with (
            mock.patch.object(pps.supermemory, "is_configured", return_value=True),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_file_name",
                new=mock.AsyncMock(return_value=fake_doc),
            ) as find_mock,
            mock.patch.object(
                pps.supermemory,
                "document_fetch_key",
                return_value="drive:abc",
            ),
            mock.patch.object(
                pps.supermemory,
                "get_document_content",
                new=mock.AsyncMock(return_value=body),
            ) as get_mock,
            mock.patch.object(
                pps,
                "search_knowledge_base",
                new=mock.AsyncMock(
                    side_effect=AssertionError("search must not run when pin works")
                ),
            ),
        ):
            text, sources = await pps._fetch_guide_context(_rfp(), "")
        self.assertIn("5.3 Monthly", text)
        self.assertEqual(sources, ["00_Guide_Pricing.docx"])
        find_mock.assert_awaited()
        get_mock.assert_awaited_once()

    async def test_fetch_guide_falls_back_to_search_when_pin_missing(self) -> None:
        with (
            mock.patch.object(pps.supermemory, "is_configured", return_value=True),
            mock.patch.object(
                pps.supermemory,
                "find_document_by_file_name",
                new=mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                pps,
                "search_knowledge_base",
                new=mock.AsyncMock(return_value=("search-fallback-guide", ["hit.docx"])),
            ),
        ):
            text, sources = await pps._fetch_guide_context(_rfp(title="T", client="C"), "")
        self.assertIn("search-fallback-guide", text)
        self.assertIn("hit.docx", sources)
