import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
from app.services.proposal_intelligence.schemas import RetrievalEntry


class JitRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_for_section_uses_planned_queries(self) -> None:
        entry = RetrievalEntry(
            sectionId="rfp-sec-1",
            requiredAssets=["methodology docs"],
            queries=["agile website methodology"],
            expectedSources=["methodology"],
            whyNeeded="Support methodology",
        )
        fake_hit = {
            "id": "doc-1",
            "customId": "methodology_guide.pdf",
            "content": "Discovery then UX then development.",
            "metadata": {"fileName": "methodology_guide.pdf"},
        }
        with patch(
            "app.services.proposal_intelligence.jit_retrieval.supermemory.is_configured",
            return_value=True,
        ), patch(
            "app.services.proposal_intelligence.jit_retrieval.supermemory.search_documents",
            new_callable=AsyncMock,
            return_value=[fake_hit],
        ), patch(
            "app.services.proposal_intelligence.jit_retrieval.supermemory.is_knowledge_base_hit",
            return_value=True,
        ):
            items = await retrieve_for_section(entry, rfp_client="City", start_index=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "E1")
        self.assertIn("rfp-sec-1", items[0].section_ids)


if __name__ == "__main__":
    unittest.main()
