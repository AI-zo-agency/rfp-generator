import unittest

from app.services.proposal_intelligence.assembler import derive_legacy_fields, refresh_proposal_memory
from app.services.proposal_intelligence.agents.validation import run_validate_plan
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
    RetrievalEntry,
    RetrievalPlan,
    SectionPlan,
    SectionPlans,
)


def _sample_ready_plan() -> ProposalExecutionPlan:
    plan = ProposalExecutionPlan(rfpId="rfp-1")
    plan.opportunity.understanding.client = "City of Test"
    plan.opportunity.understanding.project_type = "Website Redesign"
    plan.opportunity.understanding.confidence = 0.9
    plan.opportunity.strategy.confidence = 0.9
    plan.delivery.delivery_model.type = "Agile"
    plan.delivery.delivery_model.confidence = 0.85
    plan.delivery.methodology.confidence = 0.8
    plan.delivery.budget.pricing_model = "Fixed Fee"
    plan.delivery.budget.confidence = 0.8
    plan.delivery.timeline.confidence = 0.8
    plan.writing.proposal_outline = ProposalOutline(
        sections=[
            OutlineSection(id="rfp-sec-1", title="Methodology", order=1, required=True),
        ],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(
        plans=[
            SectionPlan(
                sectionId="rfp-sec-1",
                title="Methodology",
                purpose="Explain delivery",
                keyMessages=["Collaborative"],
                evidenceNeeded=["Internal methodology"],
                retrievalGoal="methodology docs",
                writerInstructions="Avoid repeating exec summary",
                successDefinition="Evaluator understands execution confidence",
            )
        ],
        confidence=0.85,
    )
    plan.writing.retrieval_plan = RetrievalPlan(
        entries=[
            RetrievalEntry(
                sectionId="rfp-sec-1",
                requiredAssets=["methodology docs"],
                queries=["agile website methodology"],
                expectedSources=["methodology"],
                whyNeeded="Support methodology section",
            )
        ],
        confidence=0.85,
    )
    return plan


class AssemblerTests(unittest.TestCase):
    def test_phase2_research_evidence_always_empty(self) -> None:
        legacy = derive_legacy_fields(_sample_ready_plan())
        self.assertNotIn("evidenceCorpus", legacy)
        self.assertTrue(legacy["rfpSections"])
        self.assertIn("rfp-sec-1", legacy["sectionQueries"])

    def test_low_budget_confidence_warns(self) -> None:
        plan = _sample_ready_plan()
        plan.delivery.budget.confidence = 0.4
        plan = run_validate_plan(plan)
        self.assertTrue(
            any("budget" in x.lower() for x in plan.validation.low_confidence_artifacts)
        )
        self.assertEqual(plan.validation.readiness_status, "ready")

    def test_refresh_memory(self) -> None:
        plan = _sample_ready_plan()
        plan = refresh_proposal_memory(plan)
        self.assertEqual(plan.proposal_memory.facts.get("clientName"), "City of Test")
        self.assertEqual(plan.proposal_memory.facts.get("pricingModel"), "Fixed Fee")


if __name__ == "__main__":
    unittest.main()
