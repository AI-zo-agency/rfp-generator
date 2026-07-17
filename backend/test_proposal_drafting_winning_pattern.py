import unittest
import sys
import types

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover - import stub for formatter-only test
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.services.proposal_drafting_graph import (
    _empty_draft_fallback,
    _format_plan_context,
    _is_plan_driven_narrative,
)


class ProposalDraftingWinningPatternTests(unittest.TestCase):
    def test_format_plan_context_includes_winning_pattern_without_prior_prose(self) -> None:
        context = _format_plan_context(
            {
                "execution_plan": {
                    "writing": {
                        "sectionPlans": {
                            "plans": [
                                {
                                    "sectionId": "methodology",
                                    "purpose": "Explain delivery approach.",
                                    "winningPattern": {
                                        "openingPattern": "Start with client understanding.",
                                        "structureFlow": [
                                            "Challenge",
                                            "Approach",
                                            "Phases",
                                        ],
                                        "persuasionTechniques": ["Risk reduction"],
                                        "recommendedWordCount": 900,
                                        "avoid": ["Generic marketing language"],
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            "methodology",
        )

        self.assertIn("Winning Pattern", context)
        self.assertIn("Start with client understanding.", context)
        self.assertIn("Do not copy prior proposal prose", context)

    def test_format_plan_context_includes_opportunity_understanding(self) -> None:
        context = _format_plan_context(
            {
                "execution_plan": {
                    "opportunity": {
                        "understanding": {
                            "client": "MDWFP",
                            "painPoints": ["Seasonal license demand"],
                            "businessGoals": ["Grow park reservations"],
                        },
                        "strategy": {
                            "winningTheme": "Seasonal outdoor media partnership",
                        },
                    },
                    "writing": {
                        "sectionPlans": {
                            "plans": [
                                {
                                    "sectionId": "understanding",
                                    "purpose": "Restate the client's challenge.",
                                }
                            ]
                        }
                    },
                }
            },
            "understanding",
        )

        self.assertIn("Opportunity Understanding", context)
        self.assertIn("Seasonal license demand", context)

    def test_understanding_is_plan_driven_narrative(self) -> None:
        self.assertTrue(
            _is_plan_driven_narrative(
                title="Understanding of Requirements",
                register="narrative",
            )
        )

    def test_empty_narrative_fallback_does_not_blame_corpus(self) -> None:
        fallback = _empty_draft_fallback(
            title="Understanding of Requirements",
            register="narrative",
            requirements=["Address Understanding of Requirements per RFP"],
            has_plan_context=True,
        )

        self.assertNotIn("insufficient evidence in corpus", fallback)
        self.assertIn("Understanding of Requirements", fallback)


if __name__ == "__main__":
    unittest.main()
