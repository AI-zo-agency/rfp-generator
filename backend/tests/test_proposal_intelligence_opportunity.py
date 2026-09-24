import unittest

from app.services.proposal_intelligence.agents.rfp_understanding import (
    UNDERSTANDING_FORBIDDEN_KEYS,
)
from app.services.proposal_intelligence.schemas import (
    OpportunityStrategy,
    OpportunityUnderstanding,
)


class OpportunityAgentTests(unittest.TestCase):
    def test_understanding_has_no_prose_fields(self) -> None:
        self.assertIn("content", UNDERSTANDING_FORBIDDEN_KEYS)
        self.assertIn("proposalText", UNDERSTANDING_FORBIDDEN_KEYS)

    def test_understanding_fixture_validates(self) -> None:
        u = OpportunityUnderstanding.model_validate(
            {
                "client": "City of Test",
                "industry": "Government",
                "orgType": "Municipality",
                "projectType": "Website Redesign",
                "services": ["UX", "Development"],
                "complexity": "medium",
                "confidence": 0.9,
            }
        )
        self.assertEqual(u.client, "City of Test")
        self.assertEqual(u.org_type, "Municipality")

    def test_understanding_extended_intel_fields(self) -> None:
        u = OpportunityUnderstanding.model_validate(
            {
                "client": "County",
                "projectType": "Outreach",
                "contractStructure": "Initial term + four option years",
                "memoryFacts": {"clientName": "County", "organizationType": "County"},
                "timelineIntel": {
                    "questionsDue": "September 21, 2026",
                    "quotesDue": "October 30, 2026",
                    "initialTermStart": "July 1, 2027",
                },
                "budgetIntel": {
                    "basePricingModel": "Hourly unit rates with annual NTE totals",
                    "disclosedBudget": None,
                },
            }
        )
        self.assertEqual(u.contract_structure, "Initial term + four option years")
        self.assertEqual(u.memory_facts["clientName"], "County")
        self.assertEqual(u.timeline_intel.questions_due, "September 21, 2026")

    def test_strategy_includes_extended_fields(self) -> None:
        s = OpportunityStrategy.model_validate(
            {
                "winningTheme": "Accessible by design",
                "whyUs": "Local municipal expertise",
                "executiveNarrative": "Strategic arc",
                "primaryEvaluatorConcerns": ["Accessibility", "Cost"],
                "competitivePosition": "Specialist",
                "confidence": 0.95,
            }
        )
        self.assertEqual(s.winning_theme, "Accessible by design")
        self.assertIn("Accessibility", s.primary_evaluator_concerns)


if __name__ == "__main__":
    unittest.main()
