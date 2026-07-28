"""Unit tests for KB RAG (no live Supermemory, no static topic packs)."""

from __future__ import annotations

import unittest

from app.services.kb_rag_retrieve import (
    expand_kb_queries,
    extract_relevant_windows,
    is_source_rfp_filename,
    pack_hit_context,
    prefer_agency_evidence_filename,
    rank_hits_for_question,
)


class ExpandKbQueriesTests(unittest.TestCase):
    def test_uses_question_as_is_no_static_topic_pack(self) -> None:
        queries = expand_kb_queries("what were KPIs for TORRENT LABORATORY")
        self.assertEqual(queries, ["what were KPIs for TORRENT LABORATORY"])

    def test_financial_question_not_rewritten_to_static_phrases(self) -> None:
        queries = expand_kb_queries("Financial Stability Attachment G")
        self.assertEqual(queries, ["Financial Stability Attachment G"])
        blob = " | ".join(queries).casefold()
        self.assertNotIn("profitable operation", blob)
        self.assertNotIn("07_fin proposal financial", blob)


class FilenamePreferenceTests(unittest.TestCase):
    def test_detects_source_rfp_files(self) -> None:
        self.assertTrue(is_source_rfp_filename("06_WON_CityofSantaClara_RFP_2025.pdf"))
        self.assertFalse(is_source_rfp_filename("06_WON_MaricopaCounty_Proposal_2025.pdf"))

    def test_agency_proposal_beats_source_rfp(self) -> None:
        self.assertGreater(
            prefer_agency_evidence_filename("07_FIN_CityofNorthGlenn_Proposal_2026.pdf"),
            prefer_agency_evidence_filename("06_WON_CityofSantaClara_RFP_2025.pdf"),
        )


class RankAndPackTests(unittest.TestCase):
    def test_ranks_proposal_with_term_overlap_above_irrelevant_rfp(self) -> None:
        hits = [
            {
                "title": "06_WON_CityofSantaClara_RFP_2025.pdf",
                "content": "SECTION 1. PURPOSE procurement guidelines integrity",
                "score": 0.9,
            },
            {
                "title": "07_FIN_CityofNorthGlenn_Proposal_2026.pdf",
                "content": (
                    "FINANCIAL STABILITY zö agency continuous profitable operation "
                    "strong cash flow no liens"
                ),
                "score": 0.7,
            },
        ]
        ranked = rank_hits_for_question(
            hits,
            "Financial Stability continuous profitable operation",
        )
        self.assertEqual(
            ranked[0]["title"],
            "07_FIN_CityofNorthGlenn_Proposal_2026.pdf",
        )

    def test_extract_windows_keeps_matching_section(self) -> None:
        doc = (
            "TOC fluff " * 200
            + "\n# FINANCIAL STABILITY\n"
            + "zö agency has been in continuous profitable operation for 12+ years.\n"
            + "Strong cash flow and no liens.\n"
            + "More fluff " * 200
        )
        windows = extract_relevant_windows(
            doc,
            "financial stability profitable cash flow",
            max_chars=800,
        )
        self.assertIn("FINANCIAL STABILITY", windows)
        self.assertIn("profitable operation", windows)

    def test_pack_includes_full_doc_when_it_fits(self) -> None:
        intro = "CASE STUDIES\n\n# TORRENT LABORATORY\n\nIntro only.\n"
        kpi = "# KPIs\n\n- Modernize high-visibility website pages\n"
        full = intro + kpi
        hit = {"title": "03_CS_TorrentLaboratories.pdf", "content": intro}
        packed = pack_hit_context(
            hit,
            full_document=full,
            question="what were KPIs for TORRENT LABORATORY",
            max_chars=2500,
        )
        self.assertIn("# KPIs", packed)
        self.assertIn("Modernize high-visibility website pages", packed)


if __name__ == "__main__":
    unittest.main()
