"""RFP requirements drive the capability matrix; the model does not author it.

Previously the planner returned free-text KB queries untied to any requirement,
so hits could not be attributed and the matrix was whatever the model wrote —
including "Verified" rows for capabilities absent from the KB.
"""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_capability import (
    build_matrix_from_requirements,
    derive_technical_capability_score,
    unverified_core_requirements,
)
from app.services.go_no_go_requirements import (
    RfpRequirement,
    all_queries,
    parse_requirements,
)


def _hit(name: str, content: str) -> dict:
    return {"title": name, "content": content}


class ParseRequirementsTests(unittest.TestCase):
    def test_parses_and_preserves_core_flag(self) -> None:
        out = parse_requirements(
            {
                "requirements": [
                    {
                        "requirement": "CMS implementation",
                        "category": "technical",
                        "isCore": True,
                        "rfpQuote": "vendor shall implement a CMS",
                        "kbQueries": ["zö agency CMS Drupal WordPress developer"],
                    }
                ]
            }
        )
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].is_core)
        self.assertEqual(out[0].category, "technical")

    def test_requirement_without_queries_still_searchable(self) -> None:
        out = parse_requirements(
            {"requirements": [{"requirement": "Content migration"}]}
        )
        self.assertTrue(out[0].kb_queries, "must fall back to a derived query")

    def test_duplicates_and_junk_dropped(self) -> None:
        out = parse_requirements(
            {
                "requirements": [
                    {"requirement": "UX design"},
                    {"requirement": "ux design"},
                    {"requirement": "x"},
                    "not a dict",
                ]
            }
        )
        self.assertEqual(len(out), 1)

    def test_bad_category_falls_back(self) -> None:
        out = parse_requirements(
            {"requirements": [{"requirement": "Hosting", "category": "nonsense"}]}
        )
        self.assertEqual(out[0].category, "service")

    def test_malformed_payload_is_empty(self) -> None:
        self.assertEqual(parse_requirements({}), [])
        self.assertEqual(parse_requirements({"requirements": "nope"}), [])

    def test_all_queries_dedupes(self) -> None:
        reqs = [
            RfpRequirement(requirement="A", kbQueries=["q1", "q2"]),
            RfpRequirement(requirement="B", kbQueries=["q2", "q3"]),
        ]
        self.assertEqual(all_queries(reqs), ["q1", "q2", "q3"])


class MatrixFromRequirementsTests(unittest.TestCase):
    REQS = [
        RfpRequirement(requirement="CMS implementation", isCore=True),
        RfpRequirement(requirement="WordPress website development", isCore=True),
        RfpRequirement(requirement="Brand identity design", isCore=False),
    ]

    HITS = {
        "CMS implementation": [],  # nothing in the KB
        "WordPress website development": [
            _hit(
                "04_Bio_ShawnDiCriscio.pdf",
                "Shawn DiCriscio, Web Developer. 10+ years building WordPress "
                "websites for organizations.",
            )
        ],
        "Brand identity design": [
            _hit(
                "03_CS_CityOfBend.pdf",
                "Brand identity design, signage and template library for the "
                "City of Bend.",
            )
        ],
    }

    def test_requirement_without_evidence_is_a_gap(self) -> None:
        rows = build_matrix_from_requirements(self.REQS, self.HITS)
        cms = next(r for r in rows if r.requirement == "CMS implementation")
        self.assertEqual(cms.status, "gap")
        self.assertIn("no KB results", cms.downgrade_reason)

    def test_requirement_with_evidence_is_verified_and_cited(self) -> None:
        rows = build_matrix_from_requirements(self.REQS, self.HITS)
        wp = next(
            r for r in rows if r.requirement == "WordPress website development"
        )
        self.assertEqual(wp.status, "verified")
        self.assertTrue(wp.kb_source)
        self.assertIn("Shawn", wp.evidence)

    def test_every_requirement_appears(self) -> None:
        rows = build_matrix_from_requirements(self.REQS, self.HITS)
        self.assertEqual(len(rows), len(self.REQS))

    def test_core_gaps_and_score_follow_evidence(self) -> None:
        rows = build_matrix_from_requirements(self.REQS, self.HITS)
        self.assertEqual(unverified_core_requirements(rows), ["CMS implementation"])
        score = derive_technical_capability_score(rows)
        self.assertIsNotNone(score)
        self.assertLess(score, 5)

    def test_irrelevant_evidence_is_still_a_gap(self) -> None:
        """The decisive case: hits came back, but none support the requirement.

        Without this the suite passes even with the support check disabled,
        because a requirement with zero hits is a gap either way.
        """
        reqs = [RfpRequirement(requirement="CMS implementation", isCore=True)]
        hits = {
            "CMS implementation": [
                _hit(
                    "03_CS_CityOfBend.pdf",
                    "Branding, signage and stakeholder engagement for the City "
                    "of Bend. Content development for campaign guides.",
                )
            ]
        }

        rows = build_matrix_from_requirements(reqs, hits)

        self.assertEqual(rows[0].status, "gap", rows[0].kb_source)
        self.assertIn("no retrieved KB document evidences", rows[0].downgrade_reason)

    def test_no_requirements_yields_no_rows(self) -> None:
        self.assertEqual(build_matrix_from_requirements([], {}), [])



class SchemaDriftTests(unittest.TestCase):
    """A model-authored capability matrix must never break validation.

    Live 502: the model returned status "Verified"/"Gap" (capitalised, as it
    writes them in the report). GoNoGoAnalysis declares a strict Literal, so
    Pydantic raised ValidationError and the whole analysis failed with 502 —
    over a field that is discarded and rebuilt from RFP requirements anyway.
    """

    def _raw(self, matrix):
        return {
            "summary": "s",
            "stageOneReport": "r",
            "recommendation": "review",
            "fitScore": 3,
            "worthScore": 3,
            "capabilityMatrix": matrix,
            "scopeMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "sectorMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
            "compliance": {"summary": "ok", "scoreImpact": "", "flags": []},
            "teamMatch": {"summary": "ok", "scoreImpact": "", "flags": []},
        }

    def test_capitalised_status_does_not_break_validation(self) -> None:
        from app.models.go_no_go import GoNoGoAnalysis
        from app.services.go_no_go_service import _coerce_go_no_go_raw

        raw = self._raw(
            [{"requirement": "CMS", "status": "Verified", "kbSource": "x"}]
        )
        analysis = GoNoGoAnalysis.model_validate(_coerce_go_no_go_raw(raw))
        self.assertEqual(analysis.capability_matrix, [])

    def test_garbage_matrix_shapes_are_tolerated(self) -> None:
        from app.models.go_no_go import GoNoGoAnalysis
        from app.services.go_no_go_service import _coerce_go_no_go_raw

        for matrix in ("not a list", [{"nope": 1}], [None], 42, {}):
            raw = self._raw(matrix)
            analysis = GoNoGoAnalysis.model_validate(_coerce_go_no_go_raw(raw))
            self.assertEqual(analysis.capability_matrix, [], matrix)

    def test_snake_case_key_is_also_dropped(self) -> None:
        from app.models.go_no_go import GoNoGoAnalysis
        from app.services.go_no_go_service import _coerce_go_no_go_raw

        raw = self._raw([])
        raw.pop("capabilityMatrix")
        raw["capability_matrix"] = [{"requirement": "X", "status": "GAP"}]
        analysis = GoNoGoAnalysis.model_validate(_coerce_go_no_go_raw(raw))
        self.assertEqual(analysis.capability_matrix, [])

    def test_null_decision_matrix_scores_are_filled_not_retried(self) -> None:
        """Truncated Sonnet output left score=null and used to force a full re-run."""
        from app.models.go_no_go import GoNoGoAnalysis
        from app.services.go_no_go_service import _coerce_go_no_go_raw

        raw = self._raw([])
        raw["fitScore"] = 4
        raw["worthScore"] = 3
        raw["recommendation"] = "review"
        raw["decisionMatrix"] = [
            {"dimension": "Technical Capability Match", "score": None, "notes": ""},
            {"dimension": "Resource Availability", "score": None, "notes": ""},
            {"dimension": "Financial Viability", "score": None, "notes": ""},
            {"dimension": "Strategic Value", "score": None, "notes": ""},
            {"dimension": "Win Probability", "score": None, "notes": ""},
        ]
        analysis = GoNoGoAnalysis.model_validate(_coerce_go_no_go_raw(raw))
        self.assertEqual(len(analysis.decision_matrix), 5)
        self.assertTrue(all(isinstance(row.score, int) for row in analysis.decision_matrix))
        self.assertEqual(analysis.decision_matrix[0].score, 4)


class ScoreCoherenceTests(unittest.TestCase):
    """Dimensions downstream of capability cannot outrun it.

    Live output: Technical Capability Match 0/5 (20 core requirements
    unevidenced) beside Win Probability 4/5 and Resource Availability 4/5,
    averaging to a "moderate" 3.0 on work zö cannot deliver.
    """

    def _analysis(self, matrix_scores):
        from app.models.go_no_go import (
            GoNoGoAnalysis, GoNoGoDecisionMatrixRow, GoNoGoDimension,
        )

        def dim():
            return GoNoGoDimension(summary="ok", scoreImpact="neutral", flags=[])

        return GoNoGoAnalysis(
            summary="Strong technical capability match. Overall Go Score 3.8/5.",
            recommendation="go", fitScore=4, worthScore=3,
            stageOneReport="## FINAL RECOMMENDATION\nGO WITH CONDITIONS\n",
            scopeMatch=dim(), sectorMatch=dim(), compliance=dim(), teamMatch=dim(),
            capabilityMatrix=[
                GoNoGoCapabilityRow(requirement="CMS implementation",
                                    status="gap", isCore=True),
                GoNoGoCapabilityRow(requirement="Content migration",
                                    status="gap", isCore=True),
            ],
            decisionMatrix=[
                GoNoGoDecisionMatrixRow(dimension=d, score=s, notes="")
                for d, s in matrix_scores.items()
            ],
        )

    def _enforced(self, matrix_scores):
        from app.services.go_no_go_service import _enforce_capability_evidence
        return _enforce_capability_evidence(self._analysis(matrix_scores), [])

    def test_win_probability_cannot_exceed_capability(self) -> None:
        out = self._enforced({
            "Technical Capability Match": 4, "Resource Availability": 4,
            "Financial Viability": 3, "Strategic Value": 4, "Win Probability": 4,
        })
        by_dim = {r.dimension: r.score for r in out.decision_matrix}
        self.assertEqual(by_dim["Technical Capability Match"], 0)
        self.assertLessEqual(by_dim["Win Probability"], 1)
        self.assertLessEqual(by_dim["Resource Availability"], 1)

    def test_independent_dimensions_are_untouched(self) -> None:
        out = self._enforced({
            "Technical Capability Match": 4, "Resource Availability": 4,
            "Financial Viability": 3, "Strategic Value": 4, "Win Probability": 4,
        })
        by_dim = {r.dimension: r.score for r in out.decision_matrix}
        self.assertEqual(by_dim["Financial Viability"], 3)
        self.assertEqual(by_dim["Strategic Value"], 4)

    def test_overall_score_reflects_the_capping(self) -> None:
        from app.services.go_no_go_service import compute_overall_go_score
        scores = {
            "Technical Capability Match": 4, "Resource Availability": 4,
            "Financial Viability": 3, "Strategic Value": 4, "Win Probability": 4,
        }
        before = compute_overall_go_score(self._analysis(scores))
        after = compute_overall_go_score(self._enforced(scores))
        self.assertEqual(before, 3.8)
        self.assertLess(after, 2.5)

    def test_summary_is_reconciled_too(self) -> None:
        out = self._enforced({
            "Technical Capability Match": 4, "Win Probability": 4,
        })
        self.assertNotIn("3.8/5", out.summary)
        self.assertIn("NO-GO", out.summary)


class SecondCapabilityTableTests(unittest.TestCase):
    """Only one capability table may survive in the report."""

    REPORT = (
        "## EXECUTIVE SUMMARY\nRecommendation: GO WITH CONDITIONS.\n\n"
        "## CAPABILITY ASSESSMENT\n| Req | Status |\n| CMS | Gap |\n\n"
        "## Technical and Service Requirements vs. zö Capabilities\n"
        "| RFP Requirement | zö Capability | Status |\n"
        "| CMS implementation | Shawn DiCrisio | Verified |\n"
        "| Content migration | Documented in KB | Verified |\n\n"
        "## NEXT STEPS\nReview with Sonja.\n"
    )

    def test_second_unvalidated_table_is_removed(self) -> None:
        from app.services.go_no_go_capability import upsert_capability_section

        rows = [
            GoNoGoCapabilityRow(requirement="CMS implementation",
                                status="gap", isCore=True),
        ]
        out = upsert_capability_section(self.REPORT, rows)

        self.assertNotIn("Shawn DiCrisio", out)
        self.assertNotIn("Documented in KB", out)
        self.assertEqual(out.count("| RFP Requirement |"), 1)
        self.assertIn("## NEXT STEPS", out)


if __name__ == "__main__":
    unittest.main()
