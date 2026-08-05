"""A case study must be built from its own source document, not a search blob.

Observed on RFP manual-8d94fe76 section 3.3: the builder emitted
"[MANUAL FILL: ... - no source material available in knowledge base]" three times
for City of Umatilla, while the knowledge base contained
"03_CS_City of Umatilla_Digital Campaign_2006.pdf" with 4,862 chars of real
content.

Retrieval had not failed. The broad query returned 120,032 chars across 17
documents — four other clients' proposals, a pricing guide, a filing guide — with
the actual case study ranked 7th at roughly 4% of the payload. Under strict
"never invent" rules the writer could not identify it and took the safe exit.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.services import proposal_knowledge_base_tools as kb

STUDY = "03_CS_City of Umatilla_Digital Campaign_2006.pdf"
REAL_BODY = "CASE STUDIES CITY OF UMATILLA'S Rock the Lock music festival. " * 20


class NamedCaseStudyFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_document_is_used_and_search_is_skipped(self) -> None:
        search = mock.AsyncMock(return_value=("SEARCH BLOB", ["other.pdf"]))
        with mock.patch.object(
            kb.supermemory, "find_document_by_file_name", new=mock.AsyncMock(return_value={"id": "d1"})
        ), mock.patch.object(
            kb.supermemory, "document_fetch_key", return_value="key1"
        ), mock.patch.object(
            kb.supermemory, "get_document_content", new=mock.AsyncMock(return_value=REAL_BODY)
        ), mock.patch.object(
            kb, "search_and_fetch_full", new=search
        ):
            text, sources = await kb.fetch_single_case_study(STUDY)

        self.assertEqual(sources, [STUDY], "only the named document should be cited")
        self.assertIn("umatilla", text.lower())
        search.assert_not_awaited()

    async def test_missing_named_document_falls_back_to_search(self) -> None:
        """Not every case study has a matching filename — do not starve the writer."""
        search = mock.AsyncMock(return_value=("SEARCH BLOB", ["other.pdf"]))
        with mock.patch.object(
            kb.supermemory, "find_document_by_file_name", new=mock.AsyncMock(return_value=None)
        ), mock.patch.object(kb, "search_and_fetch_full", new=search):
            text, sources = await kb.fetch_single_case_study(STUDY)

        self.assertEqual(text, "SEARCH BLOB")
        search.assert_awaited_once()

    async def test_thin_named_document_falls_back_to_search(self) -> None:
        search = mock.AsyncMock(return_value=("SEARCH BLOB", ["other.pdf"]))
        with mock.patch.object(
            kb.supermemory, "find_document_by_file_name", new=mock.AsyncMock(return_value={"id": "d1"})
        ), mock.patch.object(
            kb.supermemory, "document_fetch_key", return_value="key1"
        ), mock.patch.object(
            kb.supermemory, "get_document_content", new=mock.AsyncMock(return_value="too short")
        ), mock.patch.object(kb, "search_and_fetch_full", new=search):
            text, _sources = await kb.fetch_single_case_study(STUDY)

        self.assertEqual(text, "SEARCH BLOB")
        search.assert_awaited_once()

    async def test_lookup_error_falls_back_rather_than_raising(self) -> None:
        search = mock.AsyncMock(return_value=("SEARCH BLOB", ["other.pdf"]))
        with mock.patch.object(
            kb.supermemory,
            "find_document_by_file_name",
            new=mock.AsyncMock(side_effect=kb.supermemory.SupermemoryError("boom")),
        ), mock.patch.object(kb, "search_and_fetch_full", new=search):
            text, _sources = await kb.fetch_single_case_study(STUDY)

        self.assertEqual(text, "SEARCH BLOB")
        search.assert_awaited_once()

    async def test_named_document_is_capped_at_max_chars(self) -> None:
        with mock.patch.object(
            kb.supermemory, "find_document_by_file_name", new=mock.AsyncMock(return_value={"id": "d1"})
        ), mock.patch.object(
            kb.supermemory, "document_fetch_key", return_value="key1"
        ), mock.patch.object(
            kb.supermemory, "get_document_content", new=mock.AsyncMock(return_value="x" * 5000)
        ):
            text, _sources = await kb.fetch_single_case_study(STUDY, max_chars=1000)

        self.assertEqual(len(text), 1000)


if __name__ == "__main__":
    unittest.main()
