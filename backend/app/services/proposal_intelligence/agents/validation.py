"""Validation Agent — readiness + low-confidence warnings."""

from __future__ import annotations

from app.services.proposal_intelligence.plan_ops import append_decision
from app.services.proposal_intelligence.schemas import (
    CONFIDENCE_WARN_THRESHOLD,
    PlanValidation,
    ProposalExecutionPlan,
)

AGENT = "validation"


def run_validate_plan(plan: ProposalExecutionPlan) -> ProposalExecutionPlan:
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []
    low: list[str] = []

    u = plan.opportunity.understanding
    if not (u.client and u.project_type):
        blockers.append("Opportunity understanding missing client or projectType")
    else:
        checks.append("understanding.has_client_and_project_type")

    if not plan.writing.proposal_outline.sections:
        blockers.append("Proposal outline is empty")
    else:
        checks.append("outline.non_empty")

    if plan.writing.proposal_outline.sections and not plan.writing.retrieval_plan.entries:
        blockers.append("Retrieval plan empty while outline has sections")
    elif plan.writing.retrieval_plan.entries:
        checks.append("retrieval_plan.non_empty")

    artifact_scores: list[tuple[str, float]] = [
        ("opportunity.understanding", u.confidence),
        ("opportunity.strategy", plan.opportunity.strategy.confidence),
        ("delivery.deliveryModel", plan.delivery.delivery_model.confidence),
        ("delivery.methodology", plan.delivery.methodology.confidence),
        ("delivery.budget", plan.delivery.budget.confidence),
        ("delivery.timeline", plan.delivery.timeline.confidence),
        ("writing.proposalOutline", plan.writing.proposal_outline.confidence),
        ("writing.sectionPlans", plan.writing.section_plans.confidence),
        ("writing.retrievalPlan", plan.writing.retrieval_plan.confidence),
    ]
    for name, score in artifact_scores:
        if score > 0 and score < CONFIDENCE_WARN_THRESHOLD:
            low.append(name)
            warnings.append(f"{name} confidence low ({score:.2f}) — needs human review")

    readiness = "blocked" if blockers else "ready"
    plan.validation = PlanValidation(
        readinessStatus=readiness,
        blockers=blockers,
        warnings=warnings,
        consistencyChecks=checks,
        lowConfidenceArtifacts=low,
    )
    plan.metadata.validation_status = readiness
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Validation {readiness}",
        reason="; ".join(blockers) if blockers else f"{len(warnings)} warnings",
        confidence=1.0 if readiness == "ready" else 0.4,
    )
    return plan


async def run_validation_agent(*, plan: ProposalExecutionPlan) -> ProposalExecutionPlan:
    return run_validate_plan(plan)
