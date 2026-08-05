"""Opportunity class + compensation caps for accurate Go/No-Go scoring."""

from __future__ import annotations

import copy
import unittest

from app.services.go_no_go_opportunity import (
    apply_opportunity_score_caps,
    classify_opportunity,
)
from app.services.go_no_go_service import _extract_rfp_hard_facts


TUOLUMNE_LIKE = """
Tuolumne County Seal and Logo Design Competition
Section 1.1 Introduction. The County invites artists, designers, and community members
to submit original seal and logo design concepts for a new official County seal and logo.
This is a community design competition. Submit your design by August 30, 2026.
Contact: Liz Peterson, Deputy County Administrator.
Sealed Bid Process: Yes (Bids Sealed / Pricing Sealed)
Budget and any professional-services fee are not mentioned in this excerpt.
"""

NORMAL_PAID_RFP = """
Request for Proposals — Brand Strategy and Creative Services
The City seeks a qualified marketing agency to provide brand strategy, creative development,
and ongoing campaign support under a professional services agreement.
Scope of Services: discovery, brand platform, visual identity system, style guide, templates.
Compensation shall not exceed $450,000 (fixed-price ceiling / NTE).
Evaluation criteria. Points will be awarded as follows:
Technical Approach 40 points
Experience 30 points
Cost 20 points
References 10 points
Total 100 points.
Proposals must include methodology, team bios, case studies, and a cost proposal.
"""

UNDISCLOSED_BUDGET_SERVICES = """
RFP for Digital Marketing and Communications Services.
The County requests proposals from qualified firms to deliver media planning, content,
and public outreach. Include approach, staffing plan, and fee schedule.
Contract term: 3 years. Evaluation will consider qualifications, approach, and cost.
Budget amount is not disclosed in this solicitation.
"""


def _optimistic_matrix_raw() -> dict:
    return {
        "fitScore": 3,
        "worthScore": 3,
        "recommendation": "review",
        "summary": "Mixed signals",
        "criticalGaps": [],
        "decisionMatrix": [
            {
                "dimension": "Technical Capability Match",
                "score": 3,
                "notes": "creative team",
            },
            {
                "dimension": "Resource Availability",
                "score": 4,
                "notes": "deadline ok",
            },
            {
                "dimension": "Financial Viability",
                "score": 3,
                "notes": "undisclosed budget",
            },
            {
                "dimension": "Strategic Value",
                "score": 4,
                "notes": "showcase",
            },
            {
                "dimension": "Win Probability",
                "score": 4,
                "notes": "professional edge",
            },
        ],
    }


class ClassifyOpportunityTests(unittest.TestCase):
    def test_tuolumne_like_is_open_competition_undisclosed(self) -> None:
        opp_class, compensation = classify_opportunity(TUOLUMNE_LIKE)
        self.assertEqual(opp_class, "open_competition")
        self.assertEqual(compensation, "undisclosed")

    def test_normal_paid_rfp_is_professional_services_confirmed_fee(self) -> None:
        opp_class, compensation = classify_opportunity(NORMAL_PAID_RFP)
        self.assertEqual(opp_class, "professional_services")
        self.assertEqual(compensation, "confirmed_fee")

    def test_undisclosed_budget_services_stays_professional(self) -> None:
        opp_class, compensation = classify_opportunity(UNDISCLOSED_BUDGET_SERVICES)
        self.assertEqual(opp_class, "professional_services")
        self.assertEqual(compensation, "undisclosed")

    def test_hard_facts_include_opportunity_fields(self) -> None:
        facts = _extract_rfp_hard_facts(TUOLUMNE_LIKE)
        self.assertEqual(facts.get("opportunity_class"), "open_competition")
        self.assertEqual(facts.get("compensation_signal"), "undisclosed")


class OpportunityScoreCapTests(unittest.TestCase):
    def test_open_competition_no_fee_caps_like_claude(self) -> None:
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="open_competition",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 0)
        self.assertLessEqual(raw["worthScore"], 1)
        self.assertLessEqual(by_dim["Strategic Value"], 2)
        self.assertLessEqual(by_dim["Win Probability"], 2)
        self.assertEqual(raw["recommendation"], "no_go")

    def test_apply_hard_rules_uses_extracted_hard_facts(self) -> None:
        from app.services.go_no_go_service import _apply_hard_rules

        facts = _extract_rfp_hard_facts(TUOLUMNE_LIKE)
        raw = _optimistic_matrix_raw()
        cleaned = _apply_hard_rules(
            raw,
            evaluation_points_found=False,
            hard_facts=facts,
        )
        by_dim = {r["dimension"]: r["score"] for r in cleaned["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 0)
        self.assertLessEqual(cleaned["worthScore"], 1)
        self.assertEqual(cleaned["recommendation"], "no_go")
        overall = round(sum(by_dim.values()) / len(by_dim), 1)
        self.assertLess(overall, 3.0)

    def test_caps_never_raise_scores(self) -> None:
        raw = {
            "worthScore": 0,
            "recommendation": "no_go",
            "criticalGaps": [],
            "decisionMatrix": [
                {"dimension": "Technical Capability Match", "score": 4, "notes": ""},
                {"dimension": "Resource Availability", "score": 3, "notes": ""},
                {"dimension": "Financial Viability", "score": 0, "notes": ""},
                {"dimension": "Strategic Value", "score": 1, "notes": ""},
                {"dimension": "Win Probability", "score": 1, "notes": ""},
            ],
        }
        before = copy.deepcopy(raw)
        apply_opportunity_score_caps(
            raw,
            opportunity_class="open_competition",
            compensation_signal="undisclosed",
        )
        self.assertLessEqual(raw["worthScore"], before["worthScore"])
        for old, new in zip(before["decisionMatrix"], raw["decisionMatrix"]):
            self.assertLessEqual(new["score"], old["score"])

    def test_professional_undisclosed_does_not_force_financial_zero(self) -> None:
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="professional_services",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 3)
        self.assertEqual(raw["worthScore"], 3)
        self.assertNotEqual(raw["recommendation"], "no_go")

    def test_ambiguous_no_fee_soft_caps(self) -> None:
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="ambiguous",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertLessEqual(by_dim["Financial Viability"], 1)
        self.assertLessEqual(raw["worthScore"], 2)
        self.assertEqual(raw["recommendation"], "review")


if __name__ == "__main__":
    unittest.main()
