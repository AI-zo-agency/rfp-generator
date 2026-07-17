"""Assemble Proposal Execution Plan + derive legacy Phase 2 fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProofPoint, RfpSectionMap
from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan


def refresh_proposal_memory(plan: ProposalExecutionPlan) -> ProposalExecutionPlan:
    """Consolidate known facts from opportunity/delivery into proposalMemory."""
    u = plan.opportunity.understanding
    d = plan.delivery
    facts: dict[str, str] = {
        "clientName": u.client,
        "organizationType": u.org_type,
        "industry": u.industry,
        "projectType": u.project_type,
        "complexity": u.complexity,
    }
    if d.delivery_model.type:
        facts["deliveryApproach"] = d.delivery_model.type
    if d.budget.pricing_model:
        facts["pricingModel"] = d.budget.pricing_model
    if d.budget.contract_type:
        facts["contractType"] = d.budget.contract_type
    if plan.opportunity.strategy.winning_theme:
        facts["winningTheme"] = plan.opportunity.strategy.winning_theme
    # Preserve existing memory keys (cms, hosting, accessibility, etc.)
    plan.proposal_memory = upsert_memory(plan.proposal_memory, "assembler", facts)
    return plan


def _zo_mode_for_title(title: str) -> str:
    lower = title.lower()
    if any(k in lower for k in ("team", "personnel", "staff", "bio")):
        return "select"
    if any(k in lower for k in ("experience", "case", "reference", "portfolio")):
        return "select"
    if any(k in lower for k in ("company", "qualification", "about", "firm")):
        return "pull"
    return "write"


def derive_legacy_fields(plan: ProposalExecutionPlan) -> dict[str, Any]:
    """Derive rfpSections / sectionQueries / proofPoints. Never returns evidenceCorpus."""
    plans_by_id = {p.section_id: p for p in plan.writing.section_plans.plans}
    retrieval_by_id = {e.section_id: e for e in plan.writing.retrieval_plan.entries}

    rfp_sections: list[RfpSectionMap] = []
    section_queries: dict[str, list[str]] = {}

    for section in sorted(plan.writing.proposal_outline.sections, key=lambda s: s.order):
        brief = plans_by_id.get(section.id)
        entry = retrieval_by_id.get(section.id)
        requirements: list[str] = []
        if brief:
            requirements.extend(brief.key_messages)
            requirements.extend(brief.evidence_needed)
        if not requirements:
            requirements = [f"Address {section.title} per RFP"]

        weight = None
        if brief and brief.evaluation_criteria:
            for crit in plan.opportunity.evaluation.criteria:
                if crit.name in brief.evaluation_criteria and crit.weight is not None:
                    weight = int(crit.weight)
                    break

        focus: list[str] = []
        if entry:
            focus = list(entry.expected_sources)[:6]
            section_queries[section.id] = list(entry.queries)[:5]

        rfp_sections.append(
            RfpSectionMap(
                id=section.id,
                title=section.title,
                requirements=requirements[:12],
                retrievalFocus=focus or ["company facts"],
                zoMode=_zo_mode_for_title(section.title),  # type: ignore[arg-type]
                evaluationWeight=weight,
            )
        )

    proof_points: list[ProofPoint] = []
    for brief in plan.writing.section_plans.plans:
        for need in brief.evidence_needed[:3]:
            proof_points.append(
                ProofPoint(
                    requirement=need,
                    caseStudy=need,
                    narrativeHook=brief.purpose,
                    relevance="planned",
                    sectionIds=[brief.section_id],
                    evaluationWeight=None,
                )
            )

    return {
        "rfpSections": rfp_sections,
        "sectionQueries": section_queries,
        "proofPoints": proof_points,
    }


def stamp_metadata(plan: ProposalExecutionPlan, *, rfp_id: str, provider: str | None) -> ProposalExecutionPlan:
    plan.metadata.rfp_id = rfp_id
    plan.metadata.generated_at = datetime.now(timezone.utc).isoformat()
    if provider:
        plan.metadata.provider = provider
    plan.metadata.validation_status = plan.validation.readiness_status
    confidences = [
        plan.opportunity.understanding.confidence,
        plan.opportunity.strategy.confidence,
        plan.delivery.methodology.confidence,
        plan.delivery.budget.confidence,
        plan.writing.proposal_outline.confidence,
        plan.writing.retrieval_plan.confidence,
    ]
    nonzero = [c for c in confidences if c > 0]
    plan.metadata.plan_confidence = sum(nonzero) / len(nonzero) if nonzero else 0.0
    return plan
