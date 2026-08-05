"""The ledger is the spine: one requirement, exactly one section.

Observed: an RFP listing a cover letter first in its required content, and naming
Technical Approach as a scored criterion, produced a proposal with neither. The
parsed requirement matrix existed and was passed to the outline planner as
f"Compliance item count: {len(...)}".
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalResearchCache
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_intelligence.assembler import derive_legacy_fields
from app.services.proposal_intelligence.schemas import (
    ComplianceItem,
    ComplianceMatrix,
    EvaluationAnalysis,
    EvaluationCriterion,
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
    RetrievalEntry,
    RetrievalPlan,
    SectionPlan,
    SectionPlans,
)


def _req(rid: str, text: str, **kw) -> LedgerRequirement:
    kw.setdefault("source", "required_content")
    kw.setdefault("mandatory", True)
    return LedgerRequirement(id=rid, text=text, **kw)


class LedgerReadingsTests(unittest.TestCase):
    def test_a_requirement_with_no_section_is_missing(self) -> None:
        ledger = RequirementLedger(requirements=[_req("r1", "A cover letter", satisfied_by=[])])
        self.assertEqual([r.id for r in ledger.missing()], ["r1"])

    def test_a_requirement_with_one_section_is_satisfied(self) -> None:
        ledger = RequirementLedger(requirements=[_req("r1", "A cover letter", satisfied_by=["sec-1"])])
        self.assertEqual(ledger.missing(), [])
        self.assertEqual(ledger.duplicated(), [])

    def test_a_requirement_with_three_sections_is_duplicated(self) -> None:
        """Insurance appeared in 1.5, the attachments checklist and the contract ack."""
        ledger = RequirementLedger(requirements=[
            _req("r1", "Proof of insurance", satisfied_by=["sec-1-5", "sec-attach", "sec-contract"]),
        ])
        self.assertEqual([r.id for r in ledger.duplicated()], ["r1"])

    def test_optional_requirements_are_not_missing(self) -> None:
        ledger = RequirementLedger(requirements=[
            _req("r1", "Optional appendix", mandatory=False, satisfied_by=[]),
        ])
        self.assertEqual(ledger.missing(), [])

    def test_scored_requirements_are_reported_with_points(self) -> None:
        ledger = RequirementLedger(requirements=[
            _req("r1", "Technical Approach", source="scored_criterion", points=30.0, satisfied_by=[]),
            _req("r2", "A cover letter", satisfied_by=["sec-1"]),
        ])
        scored = ledger.scored()
        self.assertEqual([r.id for r in scored], ["r1"])
        self.assertEqual(scored[0].points, 30.0)

    def test_a_missing_scored_criterion_is_reported_as_missing(self) -> None:
        """The single most expensive defect: an unscoreable criterion."""
        ledger = RequirementLedger(requirements=[
            _req("r1", "Technical Approach", source="scored_criterion", points=30.0, satisfied_by=[]),
        ])
        self.assertEqual([r.id for r in ledger.missing()], ["r1"])

    def test_empty_ledger_is_clean(self) -> None:
        ledger = RequirementLedger(requirements=[])
        self.assertEqual(ledger.missing(), [])
        self.assertEqual(ledger.duplicated(), [])


def _sample_plan_with_requirements() -> ProposalExecutionPlan:
    """A realistic Phase 2 plan reproducing both observed defects: a cover letter
    named first in required content, and Technical Approach scored at 30 pts —
    neither mapped to an outline section."""
    plan = ProposalExecutionPlan(rfpId="rfp-e2e")
    plan.opportunity.understanding.client = "City of Test"
    plan.opportunity.understanding.confidence = 0.9
    plan.opportunity.strategy.confidence = 0.9
    plan.delivery.delivery_model.confidence = 0.85
    plan.delivery.methodology.confidence = 0.8
    plan.delivery.budget.confidence = 0.8
    plan.delivery.timeline.confidence = 0.8

    plan.opportunity.compliance = ComplianceMatrix(
        items=[
            ComplianceItem(id="comp-1", requirement="A cover letter", mandatory=True),
            ComplianceItem(
                id="comp-2",
                requirement="W-9 tax form",
                mandatory=True,
                targetSection="Attachments",
            ),
        ],
        confidence=0.9,
    )
    plan.opportunity.evaluation = EvaluationAnalysis(
        criteria=[EvaluationCriterion(name="Technical Approach", weight=30.0, priorityRank=1)],
        confidence=0.9,
    )

    plan.writing.proposal_outline = ProposalOutline(
        sections=[OutlineSection(id="rfp-sec-1", title="Attachments", order=1, required=True)],
        confidence=0.85,
    )
    plan.writing.section_plans = SectionPlans(
        plans=[
            SectionPlan(
                sectionId="rfp-sec-1",
                title="Attachments",
                purpose="Include required forms",
                keyMessages=["W-9 tax form"],
                evidenceNeeded=["Signed W-9"],
                retrievalGoal="attachments",
                writerInstructions="",
                successDefinition="All required forms attached",
            )
        ],
        confidence=0.85,
    )
    plan.writing.retrieval_plan = RetrievalPlan(
        entries=[
            RetrievalEntry(
                sectionId="rfp-sec-1",
                requiredAssets=["W-9"],
                queries=["w9 form"],
                expectedSources=["forms"],
                whyNeeded="Attachments",
            )
        ],
        confidence=0.85,
    )
    return plan


class RequirementLedgerEndToEndTests(unittest.TestCase):
    """Proves the ledger is actually populated, not just modeled.

    This is the check fact_ledger never got: build_and_attach_ledger has zero
    callers and research.fact_ledger is None forever. A ledger that exists as a
    model but is never wired into ProposalResearchCache is the same failure.
    """

    def test_assembler_populates_the_persisted_requirement_ledger(self) -> None:
        plan = _sample_plan_with_requirements()
        legacy = derive_legacy_fields(plan)

        ledger = legacy.get("requirementLedger")
        self.assertIsInstance(ledger, RequirementLedger)
        self.assertTrue(ledger.requirements, "ledger must not be empty")

        scored = ledger.scored()
        self.assertTrue(scored, "ledger must retain the scored criterion")
        self.assertEqual(scored[0].text, "Technical Approach")
        self.assertEqual(scored[0].points, 30.0)

        # Neither requirement was mapped to an outline section (the real defect) —
        # both must surface as missing, not be silently dropped.
        missing_texts = {r.text for r in ledger.missing()}
        self.assertIn("A cover letter", missing_texts)
        self.assertIn("Technical Approach", missing_texts)

        # The matched, form-carried requirement should NOT be reported missing.
        self.assertNotIn("W-9 tax form", missing_texts)

        research = ProposalResearchCache(
            rfpId="rfp-e2e",
            requirementLedger=ledger,
            updatedAt="2026-08-05T00:00:00Z",
        )
        self.assertIsNotNone(research.requirement_ledger)
        self.assertTrue(research.requirement_ledger.requirements)
        self.assertTrue(research.requirement_ledger.scored())


if __name__ == "__main__":
    unittest.main()
