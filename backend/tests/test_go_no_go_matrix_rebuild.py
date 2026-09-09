"""Evidence-driven decision matrix rebuild for every RFP."""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow, GoNoGoDecisionMatrixRow
from app.services.go_no_go_capability import rebuild_decision_matrix_scores
from app.services.go_no_go_requirements import RfpRequirement
from app.services.go_no_go_service import enrich_hits_with_shared_won_case_studies


def _cap(
    requirement: str,
    status: str,
    *,
    core: bool = True,
    category: str = "service",
) -> GoNoGoCapabilityRow:
    return GoNoGoCapabilityRow(
        requirement=requirement,
        status=status,
        isCore=core,
        category=category,
    )


def _matrix(tech: int, resource: int, financial: int, strategic: int, win: int):
    return [
        GoNoGoDecisionMatrixRow(dimension="Technical Capability Match", score=tech, notes="analyst"),
        GoNoGoDecisionMatrixRow(dimension="Resource Availability", score=resource, notes="analyst"),
        GoNoGoDecisionMatrixRow(dimension="Financial Viability", score=financial, notes="analyst"),
        GoNoGoDecisionMatrixRow(dimension="Strategic Value", score=strategic, notes="analyst"),
        GoNoGoDecisionMatrixRow(dimension="Win Probability", score=win, notes="analyst"),
    ]


class MatrixRebuildTests(unittest.TestCase):
    def test_won_municipal_comms_rows_raise_technical_and_release_caps(self) -> None:
        """Alameda-style: Santa Clara WON craft proof must not leave Technical at 0."""
        rows = [
            _cap("Media relations and press support", "verified"),
            _cap("Social media management and analytics", "verified"),
            _cap("Strategic communications planning", "partial"),
            _cap("Crisis communications support", "partial"),
            _cap("Provide three client references", "gap", category="submission"),
        ]
        # Analyst wrongly scored like the broken Alameda run.
        matrix, tech = rebuild_decision_matrix_scores(
            _matrix(0, 1, 3, 3, 1), rows
        )
        by_dim = {r.dimension: r.score for r in matrix}
        self.assertGreaterEqual(tech or 0, 3)
        self.assertEqual(by_dim["Technical Capability Match"], tech)
        self.assertGreaterEqual(by_dim["Resource Availability"], 1)
        self.assertGreaterEqual(by_dim["Win Probability"], 1)
        # Caps release with Technical — Win can rise via floor when tech >= 3.
        self.assertGreaterEqual(by_dim["Win Probability"], min(3, tech or 0))
        self.assertEqual(by_dim["Financial Viability"], 3)

    def test_strategic_cannot_vastly_outrun_zero_technical(self) -> None:
        rows = [
            _cap("Website redesign", "gap"),
            _cap("CMS migration", "gap"),
        ]
        matrix, tech = rebuild_decision_matrix_scores(
            _matrix(0, 4, 3, 5, 4), rows
        )
        by_dim = {r.dimension: r.score for r in matrix}
        self.assertEqual(tech, 0)
        self.assertEqual(by_dim["Technical Capability Match"], 0)
        self.assertLessEqual(by_dim["Strategic Value"], 2)
        self.assertLessEqual(by_dim["Win Probability"], 1)
        self.assertLessEqual(by_dim["Resource Availability"], 1)

    def test_insurance_compliance_gap_does_not_zero_technical(self) -> None:
        """Insurance is a human flag — not a craft denominator row."""
        rows = [
            _cap("Media relations and press support", "verified"),
            _cap("Social media management", "verified"),
            _cap("Insurance certificate", "gap", category="compliance"),
        ]
        matrix, tech = rebuild_decision_matrix_scores(_matrix(2, 3, 3, 3, 2), rows)
        by_dim = {r.dimension: r.score for r in matrix}
        self.assertEqual(tech, 5)
        self.assertEqual(by_dim["Technical Capability Match"], 5)


class SharedWonCaseStudyEnrichmentTests(unittest.TestCase):
    def test_santa_clara_won_proposal_shared_across_craft_rows(self) -> None:
        reqs = [
            RfpRequirement(requirement="Draft press releases", category="service", isCore=True),
            RfpRequirement(requirement="Social media calendar", category="service", isCore=True),
            RfpRequirement(requirement="Insurance certificate", category="compliance", isCore=True),
        ]
        santa = {
            "id": "sc-1",
            "title": "06_WON_CityofSantaClara_Proposal_2025.pdf",
            "content": "News releases and social media across Facebook Instagram",
            "metadata": {"name": "06_WON_CityofSantaClara_Proposal_2025.pdf"},
        }
        fin = {
            "id": "fin-1",
            "title": "07_FIN_CityofSanLeandro_Proposal_2026.pdf",
            "content": "CITY OF SANTA CLARA Strategic Municipal Leadership",
            "metadata": {"name": "07_FIN_CityofSanLeandro_Proposal_2026.pdf"},
        }
        by_req = {
            "Draft press releases": [fin],
            "Social media calendar": [],
            "Insurance certificate": [],
        }
        enriched = enrich_hits_with_shared_won_case_studies(reqs, by_req, [santa, fin])
        press_titles = [
            (h.get("title") or h.get("metadata", {}).get("name"))
            for h in enriched["Draft press releases"]
        ]
        social_titles = [
            (h.get("title") or h.get("metadata", {}).get("name"))
            for h in enriched["Social media calendar"]
        ]
        self.assertTrue(any("SantaClara_Proposal" in str(t) for t in press_titles))
        self.assertTrue(any("SantaClara_Proposal" in str(t) for t in social_titles))
        # Compliance rows are not flooded with craft case studies.
        self.assertEqual(enriched["Insurance certificate"], [])


if __name__ == "__main__":
    unittest.main()
