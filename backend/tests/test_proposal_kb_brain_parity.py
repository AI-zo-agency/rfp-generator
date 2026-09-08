"""Proposal KB gather must use the same chunk-first brain path as kb_qa_loop."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services import proposal_knowledge_base_tools as kb


class ProposalKbBrainParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_fetch_full_uses_retrieve_for_question(self) -> None:
        retrieve = AsyncMock(
            return_value=(
                "Commercial General Liability $1,000,000 / $2,000,000 Next Insurance",
                ["01_companyfacts.pdf"],
                ["q"],
            )
        )
        with (
            patch.object(kb.supermemory, "is_configured", return_value=True),
            patch(
                "app.services.kb_rag_retrieve.retrieve_for_question",
                new=retrieve,
            ),
        ):
            text, sources = await kb.search_and_fetch_full(
                "Do we have COMMERCIAL GENERAL LIABILITY",
                limit=12,
                max_chars=24_000,
            )
        self.assertIn("Next Insurance", text)
        self.assertEqual(sources, ["01_companyfacts.pdf"])
        retrieve.assert_awaited()
        kwargs = retrieve.await_args.kwargs
        self.assertEqual(kwargs.get("threshold"), 0.35)
        self.assertEqual(kwargs.get("fallback_threshold"), 0.22)

    async def test_gather_bucket_uses_chunk_first_without_query_explosion(self) -> None:
        retrieve = AsyncMock(
            side_effect=[
                ("CGL limits $1M/$2M", ["a.pdf"], ["q1"]),
                ("WBENC certified", ["b.pdf"], ["q2"]),
            ]
        )
        with patch(
            "app.services.kb_rag_retrieve.retrieve_for_question",
            new=retrieve,
        ):
            text, sources = await kb._gather_bucket(
                "company",
                [
                    "zö agency insurance Commercial General Liability",
                    "WBENC WOSB certifications",
                ],
            )
        self.assertIn("CGL", text)
        self.assertIn("WBENC", text)
        self.assertEqual(retrieve.await_count, 2)
        for call in retrieve.await_args_list:
            self.assertIs(call.kwargs.get("expand_queries"), False)
            self.assertEqual(call.kwargs.get("threshold"), 0.35)


class CompanyTruthBrainParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_truth_corpus_uses_retrieve_for_question(self) -> None:
        from app.services.company_qualification.retrieval import company_queries as cq

        retrieve = AsyncMock(
            return_value=(
                "Next Insurance CGL $1,000,000 each occurrence",
                ["06_WON_CityofUmatilla_Proposal_2026.pdf"],
                ["insurance"],
            )
        )
        with (
            patch.object(cq.supermemory, "is_configured", return_value=True),
            patch(
                "app.services.kb_rag_retrieve.retrieve_for_question",
                new=retrieve,
            ),
        ):
            text, sources = await cq.fetch_company_truth_corpus(max_chars=40_000)
        self.assertIn("Next Insurance", text)
        self.assertTrue(any("Umatilla" in s for s in sources))
        self.assertGreaterEqual(retrieve.await_count, 1)
        self.assertIs(retrieve.await_args.kwargs.get("expand_queries"), False)


class ChatSectionKbBrainParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_kb_blob_uses_retrieve_for_question(self) -> None:
        from app.services import proposal_section_editor as editor

        retrieve = AsyncMock(
            return_value=(
                "Commercial General Liability $1,000,000 Next Insurance",
                ["01_companyfacts.pdf"],
                ["q"],
            )
        )
        with patch(
            "app.services.kb_rag_retrieve.retrieve_for_question",
            new=retrieve,
        ):
            blob, facts = await editor._fetch_kb_blob_for_selection(
                ["Do we have COMMERCIAL GENERAL LIABILITY"]
            )
        self.assertIn("Next Insurance", blob)
        self.assertIn("Next Insurance", facts)
        retrieve.assert_awaited()
        self.assertEqual(retrieve.await_args.kwargs.get("threshold"), 0.35)
        self.assertEqual(retrieve.await_args.kwargs.get("fallback_threshold"), 0.22)


if __name__ == "__main__":
    unittest.main()
