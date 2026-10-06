"""Opportunity class + compensation caps for accurate Go/No-Go scoring."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.services.go_no_go_opportunity import (
    OpportunityClassification,
    apply_opportunity_score_caps,
    parse_opportunity_classification,
)
from app.services.go_no_go_service import _apply_hard_rules, _extract_rfp_hard_facts


CNM_LIKE = """
Branding Services for Central New Mexico Community College
PAYMENT TERMS: NET 30 DAYS
CNM Intends to award an Indefinite Quantity Price Agreement for one (1) year.
Section VII Economy and Price — 200 points
Bidders shall submit hourly billing rates across nine service categories.
No payment will be made for the preparation of proposals or for any costs incurred prior to contract award.
Request for Proposals — Scope of Services includes brand strategy, marketing communications.
Sample Services Agreement attached.
"""

TUOLUMNE_LIKE = """
Tuolumne County Seal and Logo Design Competition
The County invites artists, designers, and community members to submit original seal and logo design concepts.
This is a community design competition. Submit your design by August 30, 2026.
Budget and any professional-services fee are not mentioned in this excerpt.
"""

FUNDING_PROGRAM_LIKE = """
Municipal Funding Opportunity — Community Project Awards
Eligible organizations may apply for project funding from the City's award pool.
Applicants must propose a project and budget ask, submit via the online application portal,
and present to the review committee. Award amounts are not fixed in the notice;
applicants define their funding request. This is not a vendor services contract.
"""

NORMAL_PAID_RFP = """
Request for Proposals — Brand Strategy and Creative Services
Scope of Services: discovery, brand platform, visual identity system.
Compensation shall not exceed $450,000 (fixed-price ceiling / NTE).
Evaluation criteria. Technical Approach 40 points. Cost 20 points. Total 100 points.
"""


def _optimistic_matrix_raw() -> dict:
    return {
        "fitScore": 3,
        "worthScore": 3,
        "recommendation": "review",
        "summary": "Mixed signals",
        "criticalGaps": [],
        "decisionMatrix": [
            {"dimension": "Technical Capability Match", "score": 3, "notes": "creative team"},
            {"dimension": "Resource Availability", "score": 4, "notes": "deadline ok"},
            {"dimension": "Financial Viability", "score": 3, "notes": "undisclosed budget"},
            {"dimension": "Strategic Value", "score": 4, "notes": "showcase"},
            {"dimension": "Win Probability", "score": 4, "notes": "professional edge"},
        ],
    }


class ParseOpportunityClassificationTests(unittest.TestCase):
    def test_cnm_prep_disclaimer_not_forced_unpaid_when_llm_says_confirmed(self) -> None:
        parsed = parse_opportunity_classification(
            {
                "opportunityClass": "professional_services",
                "compensationSignal": "confirmed_fee",
                "evidenceQuote": "PAYMENT TERMS: NET 30 DAYS",
                "rationale": "Paid price agreement with invoicing terms.",
            },
            rfp_text=CNM_LIKE,
        )
        self.assertEqual(parsed.opportunity_class, "professional_services")
        self.assertEqual(parsed.compensation_signal, "confirmed_fee")

    def test_ungrounded_unpaid_quote_downgrades(self) -> None:
        parsed = parse_opportunity_classification(
            {
                "opportunityClass": "professional_services",
                "compensationSignal": "explicitly_unpaid",
                "evidenceQuote": "this phrase is not in the rfp",
            },
            rfp_text=CNM_LIKE,
        )
        self.assertEqual(parsed.compensation_signal, "undisclosed")

    def test_quote_match_ignores_pdf_newlines(self) -> None:
        """Alameda-style: LLM quote is one line; PDF extract has a linebreak mid-sentence."""
        rfp = (
            "Proposers may propose retainer, hourly, or hybrid arrangements, "
            "provided the proposal clearly \nidentifies what is included at each "
            "price point."
        )
        parsed = parse_opportunity_classification(
            {
                "opportunityClass": "professional_services",
                "compensationSignal": "confirmed_fee",
                "evidenceQuote": (
                    "Proposers may propose retainer, hourly, or hybrid arrangements, "
                    "provided the proposal clearly identifies what is included"
                ),
            },
            rfp_text=rfp,
        )
        self.assertEqual(parsed.compensation_signal, "confirmed_fee")

    def test_funding_program_prize_signal_softens_to_undisclosed(self) -> None:
        parsed = parse_opportunity_classification(
            {
                "opportunityClass": "funding_program",
                "compensationSignal": "prize_only",
                "evidenceQuote": "apply for project funding from the City's award pool",
                "rationale": "Mislabelled funding award as prize.",
            },
            rfp_text=FUNDING_PROGRAM_LIKE,
        )
        self.assertEqual(parsed.opportunity_class, "funding_program")
        self.assertEqual(parsed.compensation_signal, "undisclosed")


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
        self.assertEqual(raw["recommendation"], "no_go")

    def test_apply_hard_rules_uses_extracted_hard_facts(self) -> None:
        facts = _extract_rfp_hard_facts(
            TUOLUMNE_LIKE,
            opportunity_class="open_competition",
            compensation_signal="undisclosed",
        )
        raw = _optimistic_matrix_raw()
        cleaned = _apply_hard_rules(
            raw,
            evaluation_points_found=False,
            hard_facts=facts,
        )
        by_dim = {r["dimension"]: r["score"] for r in cleaned["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 0)
        self.assertEqual(cleaned["recommendation"], "no_go")

    def test_professional_undisclosed_does_not_force_financial_zero(self) -> None:
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="professional_services",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 3)
        self.assertNotEqual(raw["recommendation"], "no_go")

    def test_funding_program_soft_caps_not_open_competition_hard_zero(self) -> None:
        """Any funding-award shape: soft caps — not Financial 0 / forced no_go."""
        raw = _optimistic_matrix_raw()
        raw["recommendation"] = "go"
        apply_opportunity_score_caps(
            raw,
            opportunity_class="funding_program",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 2)
        self.assertLessEqual(raw["worthScore"], 3)
        self.assertGreaterEqual(raw["worthScore"], 2)
        self.assertEqual(raw["recommendation"], "review")
        self.assertNotEqual(raw["recommendation"], "no_go")
        gaps = " ".join(str(g) for g in raw["criticalGaps"])
        self.assertIn("Funding-award program", gaps)
        self.assertNotIn("open design/community competition", gaps)
        questions = raw.get("clarifyingQuestions") or []
        self.assertTrue(
            any("applicant of record" in str(q).casefold() for q in questions),
            questions,
        )

    def test_mislabelled_grant_as_open_competition_still_hard_caps(self) -> None:
        """Until the classifier returns funding_program, open_competition stays strict."""
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="open_competition",
            compensation_signal="undisclosed",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 0)
        self.assertEqual(raw["recommendation"], "no_go")

    def test_cnm_confirmed_fee_does_not_cap_financial_to_zero(self) -> None:
        raw = _optimistic_matrix_raw()
        apply_opportunity_score_caps(
            raw,
            opportunity_class="professional_services",
            compensation_signal="confirmed_fee",
        )
        by_dim = {r["dimension"]: r["score"] for r in raw["decisionMatrix"]}
        self.assertEqual(by_dim["Financial Viability"], 3)
        self.assertNotEqual(raw["recommendation"], "no_go")


class ClassifyOpportunityLlmTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_returns_parsed_result(self) -> None:
        from app.services.go_no_go_opportunity import classify_opportunity_llm

        mock_raw = {
            "opportunityClass": "professional_services",
            "compensationSignal": "confirmed_fee",
            "evidenceQuote": "PAYMENT TERMS: NET 30 DAYS",
            "rationale": "Paid contract.",
        }
        with patch(
            "app.services.llm.chat_json",
            new_callable=AsyncMock,
            return_value=(mock_raw, "test"),
        ):
            result = await classify_opportunity_llm(CNM_LIKE, rfp_id="rfp-test")
        self.assertEqual(result.opportunity_class, "professional_services")
        self.assertEqual(result.compensation_signal, "confirmed_fee")


if __name__ == "__main__":
    unittest.main()
