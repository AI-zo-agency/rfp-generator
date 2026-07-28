"""RFP-theme case study candidate queries (parity with kb_qa_loop topical search)."""

from __future__ import annotations

import unittest

from app.services.proposal_knowledge_base_tools import (
    build_case_study_candidate_queries,
    extract_case_study_search_themes,
)


class CaseStudyCandidateQueryTests(unittest.TestCase):
    def test_public_awareness_rfp_emits_awareness_theme(self) -> None:
        themes = extract_case_study_search_themes(
            rfp_sector="government",
            rfp_context=(
                "Public Awareness Marketing for Tarrant County. "
                "Sample Work Portfolio (Minimum Two Recent Campaigns). "
                "Behavior change and community outreach required."
            ),
            services_requested=["Public Awareness Marketing", "Media Planning & Buying"],
        )
        blob = " ".join(themes).casefold()
        self.assertIn("public awareness", blob)
        self.assertTrue(any("media" in t.casefold() for t in themes))

    def test_queries_are_03_cs_prefixed_and_include_master_digest(self) -> None:
        queries = build_case_study_candidate_queries(
            rfp_sector="government",
            rfp_context="Public Awareness Marketing sample work portfolio minimum two campaigns",
            services_requested=["Public Awareness Marketing"],
        )
        self.assertGreaterEqual(len(queries), 3)
        self.assertTrue(all(q.startswith("03_CS_") for q in queries))
        self.assertTrue(
            any("AllCaseStudies" in q or "public awareness" in q.casefold() for q in queries)
        )

    def test_does_not_embed_current_buyer_name_as_theme(self) -> None:
        queries = build_case_study_candidate_queries(
            rfp_sector="government",
            rfp_context="Tarrant County Public Awareness Marketing RFP requirements.",
            services_requested=["Public Awareness Marketing"],
        )
        joined = " ".join(queries).casefold()
        self.assertNotIn("tarrant", joined)


if __name__ == "__main__":
    unittest.main()
