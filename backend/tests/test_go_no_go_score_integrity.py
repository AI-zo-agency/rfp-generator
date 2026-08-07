"""Detecting fabrication must never raise a Go/No-Go score.

Root cause this guards: _scrub_invented_eval_and_people bumped Financial
Viability / Win Probability rows from <=2 to 3 and forced worthScore to 3 when
it detected invented evaluation weights. compute_overall_go_score averages the
decision matrix, so finding fabrication mechanically *increased* the composite —
precisely when the analysis deserved least confidence.
"""

from __future__ import annotations

import copy
import unittest

from app.services.go_no_go_service import _scrub_invented_eval_and_people


def _raw_with_invented_weights() -> dict:
    return {
        "summary": "Cost is weighted 62% of total points.",
        "stageOneReport": "Evaluation: Cost 62% (Max Points 100). Technical 38 points.",
        "fitScore": 2,
        "worthScore": 2,
        "recommendation": "no_go",
        "criticalGaps": ["No municipal website precedent"],
        "decisionMatrix": [
            {"dimension": "Technical Capability Match", "score": 2, "notes": "weak"},
            {"dimension": "Resource Availability", "score": 2, "notes": "thin"},
            {"dimension": "Financial Viability", "score": 1, "notes": "Cost 62% of points"},
            {"dimension": "Strategic Value", "score": 2, "notes": "low"},
            {"dimension": "Win Probability", "score": 1, "notes": "Cost 62% weighting"},
        ],
    }


def _composite(matrix: list[dict]) -> float:
    return round(sum(r["score"] for r in matrix) / len(matrix), 1)


class ScrubberScoreIntegrityTests(unittest.TestCase):
    def test_scrub_never_raises_any_matrix_score(self) -> None:
        raw = _raw_with_invented_weights()
        before = copy.deepcopy(raw["decisionMatrix"])

        _scrub_invented_eval_and_people(raw, evaluation_points_found=False)

        for old, new in zip(before, raw["decisionMatrix"]):
            self.assertLessEqual(
                new["score"],
                old["score"],
                msg=(
                    f"{new['dimension']}: score rose {old['score']} -> {new['score']} "
                    "because fabrication was detected"
                ),
            )

    def test_scrub_never_raises_the_composite(self) -> None:
        raw = _raw_with_invented_weights()
        before = _composite(raw["decisionMatrix"])

        _scrub_invented_eval_and_people(raw, evaluation_points_found=False)

        self.assertLessEqual(_composite(raw["decisionMatrix"]), before)

    def test_scrub_never_raises_worth_score(self) -> None:
        raw = _raw_with_invented_weights()
        before = raw["worthScore"]

        _scrub_invented_eval_and_people(raw, evaluation_points_found=False)

        self.assertLessEqual(raw["worthScore"], before)

    def test_invented_weight_notes_are_still_replaced(self) -> None:
        """The legitimate half of the scrubber must keep working."""
        raw = _raw_with_invented_weights()

        _scrub_invented_eval_and_people(raw, evaluation_points_found=False)

        for row in raw["decisionMatrix"]:
            self.assertNotIn("62%", row["notes"], msg=row)

    def test_published_weights_are_left_alone(self) -> None:
        """When the extractor found a real table, nothing is scrubbed."""
        raw = _raw_with_invented_weights()
        before = copy.deepcopy(raw["decisionMatrix"])

        _scrub_invented_eval_and_people(raw, evaluation_points_found=True)

        self.assertEqual(
            [r["score"] for r in raw["decisionMatrix"]],
            [r["score"] for r in before],
        )

    def test_disclosed_percent_criteria_do_not_get_undisclosed_notes(self) -> None:
        """NYCEDC 25%×4 must not rewrite Win Probability as 'not disclosed'."""
        raw = {
            "summary": "Scored against disclosed 25% selection criteria.",
            "stageOneReport": "Win Probability reflects fee/experience at 25% each.",
            "fitScore": 3,
            "worthScore": 3,
            "recommendation": "review",
            "criticalGaps": [],
            "decisionMatrix": [
                {
                    "dimension": "Win Probability",
                    "score": 2,
                    "notes": "Against disclosed 25%/25%/25%/25% criteria.",
                },
            ],
        }

        _scrub_invented_eval_and_people(raw, evaluation_points_found=True)

        self.assertNotIn(
            "not disclosed",
            raw["decisionMatrix"][0]["notes"].casefold(),
        )


if __name__ == "__main__":
    unittest.main()
