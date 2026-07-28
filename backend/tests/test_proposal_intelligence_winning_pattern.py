import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_intelligence.agents.winning_pattern_intelligence import (
    run_winning_pattern_intelligence,
)
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ProposalOutline


class WinningPatternIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_persists_structured_patterns_without_raw_text(self) -> None:
        plan = ProposalExecutionPlan(rfpId="rfp-1")
        plan.opportunity.understanding.client = "City of Example"
        plan.opportunity.understanding.industry = "Municipality"
        plan.opportunity.understanding.services = ["Website"]
        plan.writing.proposal_outline = ProposalOutline.model_validate(
            {
                "sections": [
                    {
                        "id": "methodology",
                        "title": "Methodology",
                        "order": 1,
                        "required": True,
                    }
                ],
                "confidence": 0.9,
            }
        )

        llm_payload = {
            "patterns": [
                {
                    "sectionId": "methodology",
                    "sourceWonProposals": ["City Website Win"],
                    "openingPattern": "Start with client understanding.",
                    "structureFlow": ["Challenge", "Approach", "Phases"],
                    "persuasionTechniques": ["Risk reduction"],
                    "commonDifferentiators": ["Accessibility"],
                    "recommendedWordCount": 900,
                    "content": "This raw proposal paragraph must be stripped.",
                    "excerpt": "This excerpt must also be stripped.",
                    "confidence": 0.8,
                }
            ],
            "confidence": 0.8,
        }

        with (
            patch(
                "app.services.proposal_intelligence.agents.winning_pattern_intelligence.retrieve_intelligence",
                new=AsyncMock(
                    return_value=[
                        {
                            "source": "City Website Win",
                            "excerpt": "Won proposal text used only for pattern extraction.",
                        }
                    ]
                ),
            ),
            patch(
                "app.services.proposal_intelligence.agents.winning_pattern_intelligence.safe_chat_json",
                new=AsyncMock(return_value=(llm_payload, "test-provider")),
            ),
        ):
            updated = await run_winning_pattern_intelligence(plan=plan)

        section_plan = updated.writing.section_plans.plans[0]
        dumped = section_plan.model_dump(by_alias=True)

        self.assertEqual(section_plan.section_id, "methodology")
        self.assertEqual(
            dumped["winningPattern"]["sourceWonProposals"], ["City Website Win"]
        )
        self.assertNotIn("content", dumped["winningPattern"])
        self.assertNotIn("excerpt", dumped["winningPattern"])


if __name__ == "__main__":
    unittest.main()
