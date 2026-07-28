import unittest

from app.models.proposal import ProposalResearchCache
from app.services.proposal_intelligence.schemas import (
    CONFIDENCE_WARN_THRESHOLD,
    ProposalExecutionPlan,
    SectionPlan,
)


class ProposalIntelligenceSchemaTests(unittest.TestCase):
    def test_empty_plan_round_trip(self) -> None:
        plan = ProposalExecutionPlan(rfpId="rfp-1")
        dumped = plan.model_dump(by_alias=True)
        again = ProposalExecutionPlan.model_validate(dumped)
        self.assertEqual(again.metadata.rfp_id, "rfp-1")
        self.assertEqual(again.proposal_memory.facts, {})
        self.assertIsNone(again.writing.reviewer_personas)
        self.assertEqual(again.evidence_corpus_rule, "phase3_only")

    def test_research_cache_accepts_execution_plan(self) -> None:
        plan = ProposalExecutionPlan(rfpId="rfp-1")
        cache = ProposalResearchCache(
            rfpId="rfp-1",
            updatedAt="2026-07-17T00:00:00Z",
            proposalExecutionPlan=plan.model_dump(by_alias=True),
            evidenceCorpus=[],
        )
        self.assertEqual(cache.evidence_corpus, [])
        self.assertIsNotNone(cache.proposal_execution_plan)

    def test_confidence_threshold_constant(self) -> None:
        self.assertEqual(CONFIDENCE_WARN_THRESHOLD, 0.70)

    def test_section_plan_round_trips_winning_pattern(self) -> None:
        plan = SectionPlan.model_validate(
            {
                "sectionId": "methodology",
                "title": "Methodology",
                "winningPattern": {
                    "sourceWonProposals": ["City Website Win"],
                    "openingPattern": "Start with client understanding.",
                    "structureFlow": ["Challenge", "Approach", "Phases", "QA", "Outcomes"],
                    "persuasionTechniques": ["Risk reduction"],
                    "commonDifferentiators": ["Accessibility"],
                    "commonObjections": ["Timeline"],
                    "recommendedWordCount": 900,
                    "recommendedVisuals": ["Delivery phase diagram"],
                    "avoid": ["Generic marketing language"],
                    "commonProofThemes": ["Governance"],
                    "confidence": 0.82,
                },
            }
        )

        dumped = plan.model_dump(by_alias=True)

        self.assertEqual(
            dumped["winningPattern"]["structureFlow"],
            ["Challenge", "Approach", "Phases", "QA", "Outcomes"],
        )
        self.assertEqual(dumped["winningPattern"]["recommendedWordCount"], 900)


if __name__ == "__main__":
    unittest.main()
