"""Hard-fact extraction for Go/No-Go (budget + evaluation points)."""

from __future__ import annotations

import unittest

from app.services.go_no_go_service import _extract_rfp_hard_facts


HTA_SNIPPET = """
Section 2.4 Compensation. The contract is a fixed-price ceiling of $2,950,000.
Year 1 $900,000. Year 2 $650,000. Year 3 $1,400,000.
Transaction fee cap $5,000 does not change the ceiling.

Section 4.2 Evaluation Criteria. Points will be awarded as follows:
Overall Capabilities 35 points
Brand Marketing Plan 35 points
Familiarity with Hawaiʻi Brand 15 points
Cost Points Conversion 6 points
Price Reasonableness 9 points
Total 100 points.
"""

GSU_ELIGIBILITY_SNIPPET = """
Group 1.2.1 Small Business. A small business is an entity that has 300 or fewer employees
or $30 million or less in gross receipts per year.
Contract value is not otherwise stated. Pass/fail questions in Groups 2.3 and scored items in 3.1.
Technical Proposal approach required. Experience: 3 years minimum. References: three references.
Cost Proposal Worksheet. Oral Presentation for top three finalists.
"""


class GoNoGoHardFactsTests(unittest.TestCase):
    def test_extracts_ceiling_and_year_budgets(self) -> None:
        facts = _extract_rfp_hard_facts(HTA_SNIPPET)
        blob = " | ".join(facts["contract_value_lines"]).casefold()
        self.assertTrue(
            any("2,950,000" in line or "2950000" in line.replace(",", "") for line in facts["contract_value_lines"])
            or "2,950,000" in blob
            or "$2.95m" in blob
            or "2950000" in blob.replace(",", "").replace(".", ""),
            facts["contract_value_lines"],
        )
        self.assertTrue(
            any("900" in line for line in facts["contract_value_lines"]),
            facts["contract_value_lines"],
        )

    def test_extracts_evaluation_point_rows(self) -> None:
        facts = _extract_rfp_hard_facts(HTA_SNIPPET)
        labels = " | ".join(facts["evaluation_lines"]).casefold()
        self.assertIn("overall capabilities", labels)
        self.assertIn("brand marketing plan", labels)
        self.assertIn("35", labels)
        self.assertGreaterEqual(facts["evaluation_total"] or 0, 70)

    def test_does_not_require_first_25k_only(self) -> None:
        padded = ("x" * 40_000) + HTA_SNIPPET
        facts = _extract_rfp_hard_facts(padded)
        self.assertTrue(facts["contract_value_lines"])
        self.assertTrue(facts["evaluation_lines"])

    def test_small_business_threshold_is_not_contract_value(self) -> None:
        facts = _extract_rfp_hard_facts(GSU_ELIGIBILITY_SNIPPET)
        contract_blob = " | ".join(facts["contract_value_lines"]).casefold()
        self.assertNotIn("30", contract_blob)
        self.assertFalse(facts["evaluation_lines"])
        eligibility = " | ".join(facts.get("eligibility_dollar_lines") or []).casefold()
        self.assertIn("30", eligibility)
        self.assertIn("not contract value", eligibility)

    def test_question_group_rfp_does_not_false_extract_eval_points(self) -> None:
        """Bare 'Technical Proposal' near '3 years' must NOT become '3 points'."""
        facts = _extract_rfp_hard_facts(GSU_ELIGIBILITY_SNIPPET)
        self.assertEqual(facts["evaluation_lines"], [])
        self.assertIsNone(facts["evaluation_total"])


if __name__ == "__main__":
    unittest.main()
