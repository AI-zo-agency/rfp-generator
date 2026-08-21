"""Merged Phase 2 intelligence passes — one LLM hop per layer, same schemas."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_intelligence.merged_passes import (
    run_execution_plan,
    run_opportunity_extract,
    run_strategy_delivery,
    run_writing_briefs,
)
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
)


def _plan() -> ProposalExecutionPlan:
    return ProposalExecutionPlan(rfpId="r1")


class OpportunityExtractTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_fills_five_opportunity_artifacts(self) -> None:
        payload = {
            "understanding": {
                "client": "MSU Denver",
                "industry": "Higher education",
                "orgType": "State",
                "projectType": "Digital advertising",
                "services": ["paid media"],
                "complexity": "medium",
                "confidence": 0.9,
            },
            "compliance": {
                "items": [
                    {
                        "id": "comp-1",
                        "requirement": "Return signed addenda",
                        "mandatory": True,
                    }
                ],
                "confidence": 0.8,
            },
            "scope": {
                "mandatory": ["Program-specific campaigns"],
                "optional": ["Optional social boost"],
                "confidence": 0.85,
            },
            "evaluation": {
                "criteria": [{"name": "Approach", "weight": 40, "priorityRank": 1}],
                "emphasis": ["Approach"],
                "writingStyle": "executive",
                "confidence": 0.8,
            },
            "successCriteria": {
                "items": [
                    {
                        "criterion": "Enroll target students",
                        "why": "RFP goal",
                        "recurringTheme": True,
                    }
                ],
                "confidence": 0.75,
            },
        }
        with patch(
            "app.services.proposal_intelligence.merged_passes.llm.chat_json",
            new=AsyncMock(return_value=(payload, "test")),
        ) as chat:
            updated = await run_opportunity_extract(
                plan=_plan(),
                rfp_context="RFP body",
                rfp_meta={"title": "MSU Denver", "client": "MSU Denver", "sector": "Higher Ed"},
            )
        chat.assert_awaited_once()
        self.assertEqual(updated.opportunity.understanding.client, "MSU Denver")
        self.assertEqual(len(updated.opportunity.compliance.items), 1)
        self.assertEqual(updated.opportunity.scope.mandatory[0], "Program-specific campaigns")
        self.assertEqual(updated.opportunity.evaluation.criteria[0].name, "Approach")
        self.assertEqual(updated.opportunity.success_criteria.items[0].criterion, "Enroll target students")

    async def test_missing_client_and_project_type_is_hard_fail(self) -> None:
        with patch(
            "app.services.proposal_intelligence.merged_passes.llm.chat_json",
            new=AsyncMock(return_value=({"understanding": {}}, "test")),
        ):
            with self.assertRaises(Exception):
                await run_opportunity_extract(
                    plan=_plan(),
                    rfp_context="empty",
                    rfp_meta={"title": "", "client": "", "sector": ""},
                )


class StrategyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_fills_strategy_and_delivery(self) -> None:
        plan = _plan()
        plan.opportunity.understanding.client = "City"
        plan.opportunity.understanding.project_type = "Website"
        payload = {
            "strategy": {"winningTheme": "Local trust", "whyUs": "Oregon roots", "confidence": 0.8},
            "deliveryModel": {"type": "Hybrid", "confidence": 0.7},
            "deliveryPattern": {"phaseShape": "Discover-design-build", "confidence": 0.7},
            "methodology": {
                "phases": [{"name": "Discovery", "activities": ["kickoff"]}],
                "confidence": 0.7,
            },
            "budget": {"pricingModel": "Fixed Fee", "pricingStrategy": "Compete", "confidence": 0.6},
            "risk": {"risks": [{"risk": "Scope creep", "mitigation": "Change log"}], "confidence": 0.6},
            "qa": {"approach": "Gate reviews", "gates": ["a11y"], "confidence": 0.6},
            "communication": {"cadence": "Weekly", "confidence": 0.6},
            "training": {"trainingPlan": "Admin workshop", "confidence": 0.6},
        }
        with (
            patch(
                "app.services.proposal_intelligence.merged_passes.retrieve_intelligence",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.proposal_intelligence.merged_passes.safe_chat_json",
                new=AsyncMock(return_value=(payload, "test")),
            ) as chat,
        ):
            updated = await run_strategy_delivery(plan=plan, rfp_meta={"sector": "gov"})
        chat.assert_awaited_once()
        self.assertEqual(updated.opportunity.strategy.winning_theme, "Local trust")
        self.assertEqual(updated.delivery.delivery_model.type, "Hybrid")
        self.assertEqual(updated.delivery.methodology.phases[0].name, "Discovery")
        self.assertEqual(updated.delivery.budget.pricing_model, "Fixed Fee")
        self.assertEqual(len(updated.delivery.risk.risks), 1)
        self.assertEqual(updated.delivery.qa.gates[0], "a11y")
        self.assertEqual(updated.metadata.layer_status.opportunity, "complete")


class ExecutionPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_fills_wbs_timeline_resources(self) -> None:
        payload = {
            "workBreakdown": {
                "packages": [{"workPackage": "Kickoff", "phase": "Discovery", "deliverables": ["plan"]}],
                "confidence": 0.7,
            },
            "timeline": {"milestones": [{"name": "Go-live", "offset": "Week 12"}], "goLive": "Week 12"},
            "resources": {"allocations": [{"role": "PM", "phase": "Discovery"}]},
        }
        with patch(
            "app.services.proposal_intelligence.merged_passes.safe_chat_json",
            new=AsyncMock(return_value=(payload, "test")),
        ) as chat:
            updated = await run_execution_plan(plan=_plan())
        chat.assert_awaited_once()
        self.assertEqual(updated.delivery.work_breakdown.packages[0].work_package, "Kickoff")
        self.assertEqual(updated.delivery.timeline.go_live, "Week 12")
        self.assertEqual(updated.delivery.resources.allocations[0].role, "PM")
        self.assertEqual(updated.metadata.layer_status.delivery, "complete")


class WritingBriefsTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_fills_patterns_briefs_and_retrieval(self) -> None:
        plan = _plan()
        plan.writing.proposal_outline = ProposalOutline(
            sections=[
                OutlineSection(id="rfp-sec-1", title="Approach", order=1, required=True)
            ],
            confidence=0.8,
        )
        payload = {
            "patterns": [
                {
                    "sectionId": "rfp-sec-1",
                    "openingPattern": "Lead with the evaluator problem",
                    "confidence": 0.7,
                }
            ],
            "plans": [
                {
                    "sectionId": "rfp-sec-1",
                    "title": "Approach",
                    "purpose": "Show how we will deliver",
                    "wordBudget": 400,
                    "writerInstructions": "Hit the scored ask then stop.",
                    "retrievalGoal": "methodology evidence",
                }
            ],
            "entries": [
                {
                    "sectionId": "rfp-sec-1",
                    "requiredAssets": ["methodology"],
                    "queries": ["Find zö agency website methodology case studies with KPIs"],
                    "priority": "required",
                    "expectedSources": ["methodology"],
                    "whyNeeded": "Scored approach tab",
                }
            ],
            "confidence": 0.75,
        }
        with (
            patch(
                "app.services.proposal_intelligence.merged_passes.retrieve_intelligence",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.proposal_intelligence.merged_passes.safe_chat_json",
                new=AsyncMock(return_value=(payload, "test")),
            ) as chat,
        ):
            updated = await run_writing_briefs(plan=plan)
        chat.assert_awaited_once()
        self.assertEqual(len(updated.writing.section_plans.plans), 1)
        self.assertEqual(
            updated.writing.section_plans.plans[0].winning_pattern.opening_pattern,
            "Lead with the evaluator problem",
        )
        self.assertEqual(updated.writing.retrieval_plan.entries[0].section_id, "rfp-sec-1")
        self.assertEqual(updated.metadata.layer_status.writing, "complete")


if __name__ == "__main__":
    unittest.main()
