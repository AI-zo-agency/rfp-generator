"""Go/No-Go must survive flaky Supermemory / over-large query fan-out."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import go_no_go_service as gng
from app.services.go_no_go_requirements import RfpRequirement
from app.services.go_no_go_service import RfpContentInfo


def _rfp(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": "rfp-test",
        "title": "Design Services",
        "client": "Test City",
        "sector": "Public Sector",
        "location": "TX",
        "due_date": "2026-08-30",
        "estimated_value": None,
        "pdf_path": None,
        "description": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _content(text: str) -> RfpContentInfo:
    return RfpContentInfo(
        pdf_path=None,
        pdf_path_recorded=None,
        pdf_file_missing=False,
        pdf_exists=False,
        pdf_page_count=0,
        pdf_image_only=False,
        pdf_text=text,
        description="",
        substantive_chars=len(text),
        metadata_only=False,
    )


class GatherKnowledgeResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_transport_error_does_not_fail_gather(self) -> None:
        """A single httpx/timeout-style error must not 502 the whole analyze."""
        calls = {"n": 0}

        async def flaky_search(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise TimeoutError("simulated supermemory transport failure")
            return [
                {
                    "id": f"doc-{calls['n']}",
                    "content": "zö agency graphic design case study",
                    "metadata": {"type": "knowledge_base", "name": "03_CS_test.pdf"},
                }
            ]

        reqs = [
            RfpRequirement(
                requirement="Graphic design",
                is_core=True,
                kb_queries=["zö agency graphic design 03_CS"],
            )
        ]

        with (
            patch.object(gng.supermemory, "is_configured", return_value=True),
            patch.object(
                gng, "_plan_rfp_requirements", new=AsyncMock(return_value=reqs)
            ),
            patch.object(gng.supermemory, "search_documents", new=flaky_search),
            patch.object(gng.supermemory, "is_knowledge_base_hit", return_value=True),
            patch.object(gng, "role_evidence_queries", return_value=[]),
            patch.object(gng, "_deterministic_evidence_queries", return_value=[]),
        ):
            formatted, hits, requirements, _by = await gng._gather_knowledge_context(
                _rfp(),
                _content(
                    "The City seeks graphic design, print, banners, and social media graphics."
                ),
            )

        self.assertEqual(requirements, reqs)
        self.assertIsInstance(formatted, str)
        self.assertGreaterEqual(len(hits), 1)

    async def test_query_fanout_is_capped(self) -> None:
        seen: list[str] = []

        async def record_search(*, query: str, **kwargs):
            seen.append(query)
            return []

        reqs = [
            RfpRequirement(
                requirement=f"Req {i}",
                is_core=i < 3,
                kb_queries=[f"zö agency capability query number {i} 03_CS"],
            )
            for i in range(40)
        ]

        with (
            patch.object(gng.supermemory, "is_configured", return_value=True),
            patch.object(
                gng, "_plan_rfp_requirements", new=AsyncMock(return_value=reqs)
            ),
            patch.object(gng.supermemory, "search_documents", new=record_search),
            patch.object(gng, "role_evidence_queries", return_value=[]),
            patch.object(gng, "_deterministic_evidence_queries", return_value=[]),
        ):
            await gng._gather_knowledge_context(
                _rfp(id="rfp-test-2", title="Many Requirements RFP"),
                _content("design " * 200),
            )

        self.assertLessEqual(len(seen), gng.MAX_KB_QUERIES)
        self.assertGreater(len(seen), 0)


if __name__ == "__main__":
    unittest.main()
