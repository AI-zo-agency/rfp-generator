"""Tests for Claude Project-style Go/No-Go report assembly."""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow, GoNoGoDecisionMatrixRow
from app.services.go_no_go_report_builder import (
    build_stage_one_report,
    render_capability_assessment_table,
    render_decision_matrix_section,
    render_evaluation_criteria_table,
)


class GoNoGoReportBuilderTests(unittest.TestCase):
    def test_capability_table_uses_status_icons(self) -> None:
        rows = [
            GoNoGoCapabilityRow(
                requirement="Higher education marketing",
                status="verified",
                evidence="University of Idaho partnership",
                is_core=True,
            ),
            GoNoGoCapabilityRow(
                requirement="Standalone PR retainer list",
                status="partial",
                evidence="PR embedded in Umatilla engagement",
                is_core=False,
            ),
            GoNoGoCapabilityRow(
                requirement="Hourly billing to 15-min increments",
                status="gap",
                downgrade_reason="No approved hourly rate card in Pricing Guide",
                is_core=True,
            ),
        ]
        table = render_capability_assessment_table(rows)
        self.assertIn("✅", table)
        self.assertIn("🟡", table)
        self.assertIn("🔴", table)
        self.assertIn("University of Idaho", table)

    def test_evaluation_table_with_points(self) -> None:
        lines = [
            "I. Background & Qualifications: 200 points",
            "VI. Public Relations: 120 points",
            "VII. Economy and Price: 200 points",
        ]
        positions = [
            {
                "section": "VI. Public Relations",
                "points": 120,
                "position": "Weak — PR is integrated, not standalone retainer",
            },
            {
                "section": "VII. Economy and Price",
                "points": 200,
                "position": "Blocked — hourly rate card gap",
            },
        ]
        table = render_evaluation_criteria_table(lines, positions, total_points=1000)
        self.assertIn("1,000 points total", table)
        self.assertIn("200", table)
        self.assertIn("Blocked", table)

    def test_decision_matrix_overall_average(self) -> None:
        matrix = [
            GoNoGoDecisionMatrixRow(
                dimension="Technical Capability Match", score=4, notes="Strong HE match"
            ),
            GoNoGoDecisionMatrixRow(
                dimension="Financial Viability", score=3, notes="IDIQ paid work"
            ),
        ]
        section = render_decision_matrix_section(matrix)
        self.assertIn("3.5 / 5", section)
        self.assertIn("Technical Capability Match", section)

    def test_full_report_section_order(self) -> None:
        report = build_stage_one_report(
            compliance_snapshot=[
                "Format is portal-based — 4,000-character fields per question.",
                "NM resident preference is bonus points only.",
            ],
            capability_rows=[
                GoNoGoCapabilityRow(
                    requirement="Branding",
                    status="verified",
                    evidence="Dozens of case studies",
                    is_core=True,
                ),
            ],
            capability_summary="One of the strongest capability matches in this batch.",
            evaluation_lines=["I. Background: 200 points"],
            evaluation_positions=[
                {"section": "I. Background", "points": 200, "position": "Strong — client list"},
            ],
            evaluation_summary="320 of 1,000 points sit on PR framing and hourly rates.",
            decision_matrix=[
                GoNoGoDecisionMatrixRow(
                    dimension="Technical Capability Match", score=4, notes="U of Idaho"
                ),
            ],
            recommendation="review",
            conditions=["Resolve hourly rate card before Section VII"],
            critical_gaps=[],
            evaluation_total=1000,
        )
        compliance_idx = report.index("## Compliance Snapshot")
        cap_idx = report.index("## Capability Assessment")
        eval_idx = report.index("## Evaluation Criteria")
        matrix_idx = report.index("## Go/No-Go Decision Matrix")
        rec_idx = report.index("## Recommendation")
        self.assertLess(compliance_idx, cap_idx)
        self.assertLess(cap_idx, eval_idx)
        self.assertLess(eval_idx, matrix_idx)
        self.assertLess(matrix_idx, rec_idx)
        self.assertIn("GO WITH CONDITIONS", report)


if __name__ == "__main__":
    unittest.main()
