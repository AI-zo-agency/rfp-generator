"""A stated minimum qualification is pass/fail — it cannot be averaged away.

Root cause this guards (City of Exeter, RFP 26-05): the RFP required "at least
five comparable California municipal projects completed within the past five
years". zö had none, and 14 of 24 required capabilities — hosting, uptime SLA,
backups, security, CMS operations, the whole accessibility program — had no KB
evidence. The pipeline still returned 2.8/5 "GO WITH CONDITIONS" because:

  1. calibrate_technical_capability_score floored Technical at 3 on a raw
     head-count ratio (10 of 24 rows evidenced = 0.417 >= 0.4), while its own
     weighted base score said 2 — five of those ten rows were half-credit
     partials.
  2. that floored Technical then floored Win Probability from the analyst's
     defended 2 up to 3, off the same head-count ratio.
  3. NO-GO required derived technical <= 1, which step 1 had made unreachable.

Nothing in the model could express "this threshold is not a gap good writing
closes" — is_core only doubled a row's weight.
"""

from __future__ import annotations

import unittest

from app.models.go_no_go import (
    GoNoGoAnalysis,
    GoNoGoCapabilityRow,
    GoNoGoDecisionMatrixRow,
    GoNoGoDimension,
)
from app.services.go_no_go_capability import (
    calibrate_technical_capability_score,
    evidenced_core_craft_ratio,
    unmet_disqualifying_requirements,
)
from app.services.go_no_go_requirements import parse_requirements
from app.services.go_no_go_service import (
    _enforce_capability_evidence,
    align_recommendation_with_score,
    compute_overall_go_score,
)


def _row(
    requirement: str,
    status: str,
    *,
    category: str = "technical",
    disqualifying: bool = False,
) -> GoNoGoCapabilityRow:
    return GoNoGoCapabilityRow(
        requirement=requirement,
        status=status,
        isCore=True,
        category=category,
        kbSource="03_CS_Hampton_Lumber.pdf" if status != "gap" else "",
        evidence="sitemap reorganization and mobile-first UX"
        if status != "gap"
        else "",
        disqualifying=disqualifying,
    )


def _exeter_matrix(*, with_threshold: bool = False) -> list[GoNoGoCapabilityRow]:
    """The live Exeter shape: 5 verified, 5 partial, 14 gap."""
    rows = [
        _row(f"verified craft {i}", "verified", category="service") for i in range(5)
    ]
    rows += [_row(f"partial craft {i}", "partial") for i in range(5)]
    rows += [_row(f"missing infrastructure {i}", "gap") for i in range(14)]
    if with_threshold:
        rows.append(
            _row(
                "At least five comparable California municipal website projects "
                "completed within the past five years",
                "gap",
                category="compliance",
                disqualifying=True,
            )
        )
    return rows


def _dimension() -> GoNoGoDimension:
    return GoNoGoDimension(summary="", scoreImpact="")


def _analysis(
    rows: list[GoNoGoCapabilityRow],
    *,
    scores: dict[str, int] | None = None,
    recommendation: str = "review",
) -> GoNoGoAnalysis:
    scores = scores or {
        "Technical Capability Match": 3,
        "Resource Availability": 2,
        "Financial Viability": 3,
        "Strategic Value": 3,
        "Win Probability": 2,
    }
    return GoNoGoAnalysis(
        summary="Municipal website redesign with strong core web design craft.",
        recommendation=recommendation,
        scopeMatch=_dimension(),
        sectorMatch=_dimension(),
        compliance=_dimension(),
        teamMatch=_dimension(),
        capabilityMatrix=rows,
        decisionMatrix=[
            GoNoGoDecisionMatrixRow(dimension=name, score=score, notes="")
            for name, score in scores.items()
        ],
    )


class WeightedFloorTests(unittest.TestCase):
    def test_partials_count_half_toward_the_floor_ratio(self) -> None:
        ratio = evidenced_core_craft_ratio(_exeter_matrix())
        # 5 verified + 5 half-credit partials over 24 rows.
        self.assertAlmostEqual(ratio, 7.5 / 24, places=4)
        self.assertLess(
            ratio,
            0.4,
            msg="head-count ratio (10/24 = 0.417) must not clear the floor",
        )

    def test_exeter_shape_scores_technical_two_not_three(self) -> None:
        self.assertEqual(calibrate_technical_capability_score(_exeter_matrix()), 2)

    def test_verified_heavy_shape_still_floors_to_three(self) -> None:
        """The over-pessimism fix the floors exist for must keep working."""
        rows = [_row(f"verified {i}", "verified", category="service") for i in range(4)]
        rows += [_row("partial platform", "partial")]
        rows += [_row(f"evaluation sub-ask {i}", "gap") for i in range(5)]
        self.assertEqual(calibrate_technical_capability_score(rows), 3)

    def test_unmet_threshold_blocks_every_floor(self) -> None:
        rows = [_row(f"verified {i}", "verified", category="service") for i in range(4)]
        rows += [_row("partial platform", "partial")]
        rows += [_row(f"evaluation sub-ask {i}", "gap") for i in range(5)]
        rows.append(
            _row("mandatory contractor license", "gap", category="compliance",
                 disqualifying=True)
        )
        self.assertLess(calibrate_technical_capability_score(rows), 3)


class UnmetThresholdTests(unittest.TestCase):
    def test_gap_threshold_is_reported(self) -> None:
        blocked = unmet_disqualifying_requirements(_exeter_matrix(with_threshold=True))
        self.assertEqual(len(blocked), 1)
        self.assertIn("five comparable California", blocked[0])

    def test_evidenced_threshold_is_not_reported(self) -> None:
        rows = _exeter_matrix()
        rows.append(
            _row("three municipal references", "verified", category="compliance",
                 disqualifying=True)
        )
        self.assertEqual(unmet_disqualifying_requirements(rows), [])


class VerdictTests(unittest.TestCase):
    def test_unmet_threshold_forces_no_go(self) -> None:
        result = _enforce_capability_evidence(
            _analysis(_exeter_matrix(with_threshold=True)), []
        )
        self.assertEqual(result.recommendation, "no_go")

    def test_no_go_summary_names_the_threshold(self) -> None:
        result = _enforce_capability_evidence(
            _analysis(_exeter_matrix(with_threshold=True)), []
        )
        self.assertTrue(result.summary.startswith("NO-GO"), result.summary[:120])
        self.assertIn("five comparable California", result.summary)
        self.assertTrue(
            any("DISQUALIFYING" in gap for gap in result.critical_gaps),
            result.critical_gaps,
        )

    def test_win_probability_is_not_floored_past_a_failed_threshold(self) -> None:
        result = _enforce_capability_evidence(
            _analysis(_exeter_matrix(with_threshold=True)), []
        )
        win = next(
            row
            for row in result.decision_matrix
            if row.dimension == "Win Probability"
        )
        self.assertEqual(win.score, 2, win.notes)

    def test_capability_gaps_without_a_threshold_stay_go_with_conditions(self) -> None:
        """Fixable gaps must not become NO-GO — that regression is guarded too."""
        result = _enforce_capability_evidence(_analysis(_exeter_matrix()), [])
        self.assertEqual(result.recommendation, "review")

    def test_composite_falls_below_the_go_threshold(self) -> None:
        result = _enforce_capability_evidence(
            _analysis(_exeter_matrix(with_threshold=True)), []
        )
        overall = compute_overall_go_score(result)
        self.assertIsNotNone(overall)
        self.assertLess(overall, 2.8)


class AlignmentTests(unittest.TestCase):
    def test_high_composite_cannot_overturn_a_failed_threshold(self) -> None:
        analysis = _analysis(
            _exeter_matrix(with_threshold=True),
            scores={name: 4 for name in ("Technical Capability Match",
                                         "Resource Availability",
                                         "Financial Viability",
                                         "Strategic Value",
                                         "Win Probability")},
            recommendation="no_go",
        )
        self.assertEqual(compute_overall_go_score(analysis), 4.0)
        self.assertEqual(
            align_recommendation_with_score(analysis).recommendation, "no_go"
        )

    def test_high_composite_still_overturns_a_plain_capability_no_go(self) -> None:
        analysis = _analysis(
            _exeter_matrix(),
            scores={name: 4 for name in ("Technical Capability Match",
                                         "Resource Availability",
                                         "Financial Viability",
                                         "Strategic Value",
                                         "Win Probability")},
            recommendation="no_go",
        )
        self.assertEqual(
            align_recommendation_with_score(analysis).recommendation, "review"
        )


class RequirementPlannerTests(unittest.TestCase):
    def test_disqualifying_flag_survives_parsing(self) -> None:
        parsed = parse_requirements(
            {
                "requirements": [
                    {
                        "requirement": "Five comparable California municipal projects",
                        "category": "compliance",
                        "isCore": True,
                        "disqualifying": True,
                        "kbQueries": ["zö agency municipal website projects"],
                    },
                    {
                        "requirement": "Custom website design",
                        "category": "service",
                        "isCore": True,
                        "kbQueries": ["zö agency website design"],
                    },
                ]
            }
        )
        self.assertEqual([r.disqualifying for r in parsed], [True, False])


if __name__ == "__main__":
    unittest.main()
