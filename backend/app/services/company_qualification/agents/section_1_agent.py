"""Section 1 Agent — budgets + inclusion plan only (no prose)."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.agents.content_budget import run_content_budget_agent
from app.services.company_qualification.schemas import (
    CompanyTruth,
    PrioritizedCapabilities,
    ProposalContext,
    Section1PlanResult,
    SubsectionPlan,
)
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

SECTION_IDS = (
    "section-1-who-we-are",
    "section-1-org-structure",
    "section-1-business-info",
    "section-1-certifications",
    "section-1-insurance",
)


def _fallback_section_plan(
    *,
    content_budget,
    primary_caps: list[str],
    omit_caps: list[str],
    cert_names: list[str],
) -> dict[str, SubsectionPlan]:
    """Deterministic plan from prioritized caps + budgets — no LLM."""
    plan: dict[str, SubsectionPlan] = {}
    for sec_id in SECTION_IDS:
        b = next((x for x in content_budget.budgets if x.section_id == sec_id), None)
        targets = {"min": b.word_min if b else None, "max": b.word_max if b else None}
        if sec_id == "section-1-who-we-are":
            plan[sec_id] = SubsectionPlan(
                includedCapabilities=list(primary_caps),
                omittedCapabilities=list(omit_caps),
                targetWords=targets,
            )
        elif sec_id == "section-1-certifications":
            plan[sec_id] = SubsectionPlan(
                includedCapabilities=list(cert_names),
                omittedCapabilities=[],
                targetWords=targets,
            )
        else:
            plan[sec_id] = SubsectionPlan(
                includedCapabilities=[],
                omittedCapabilities=[],
                targetWords=targets,
            )
    return plan


async def run_section_1_agent(
    *,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
    prioritized_capabilities: PrioritizedCapabilities,
) -> tuple[Section1PlanResult, str]:
    """Plan Section 1: content budgets + per-subsection inclusion decisions."""
    content_budget, budget_provider = await run_content_budget_agent(
        proposal_context=proposal_context,
        prioritized_capabilities=prioritized_capabilities,
    )

    primary_caps = [c.capability for c in prioritized_capabilities.primary]
    secondary_caps = [c.capability for c in prioritized_capabilities.secondary]
    omit_caps = [c.capability for c in prioritized_capabilities.omit]
    relevant_certs = [c.name for c in company_truth.certifications if (c.name or "").strip()]

    try:
        raw, plan_provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Section 1 Agent for zö agency.\n"
                        "DECIDE what belongs in each Section 1 subsection — do NOT write prose.\n"
                        "Compact JSON only — no markdown fences.\n"
                        "Rules:\n"
                        "- Who We Are: primary caps only; never omit-tier\n"
                        "- Org Structure: full roster by department (plan only — no names here)\n"
                        "- Business Info / Insurance: facts from CompanyTruth\n"
                        "- Certifications: only RFP-relevant cert names from the list\n"
                        "Return:\n"
                        '{"sectionPlan":{'
                        '"section-1-who-we-are":{"includedCapabilities":[],"omittedCapabilities":[],'
                        '"targetWords":{"min":180,"max":250}},'
                        '"section-1-org-structure":{…},'
                        '"section-1-business-info":{…},'
                        '"section-1-certifications":{…},'
                        '"section-1-insurance":{…}}}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n"
                        f"Primary: {primary_caps}\n"
                        f"Secondary: {secondary_caps}\n"
                        f"Omit: {omit_caps}\n"
                        f"Certs: {relevant_certs}\n"
                        f"Budgets: {[(b.section_id, b.word_min, b.word_max, b.format) for b in content_budget.budgets]}"
                    ),
                },
            ],
            max_tokens=2048,
            temperature=0.0,
            tier="light",
        )
    except LlmError as exc:
        logger.warning(
            "Section 1 plan LLM failed (%s); using deterministic plan (no retry)",
            str(exc)[:180],
        )
        plan = Section1PlanResult(
            contentBudget=content_budget,
            sectionPlan=_fallback_section_plan(
                content_budget=content_budget,
                primary_caps=primary_caps,
                omit_caps=omit_caps,
                cert_names=relevant_certs,
            ),
        )
        return plan, budget_provider or "defaults"

    section_plan_raw = raw.get("sectionPlan") or raw.get("section_plan") or {}
    section_plan: dict[str, SubsectionPlan] = {}
    for sec_id in SECTION_IDS:
        entry = section_plan_raw.get(sec_id) or {}
        try:
            section_plan[sec_id] = SubsectionPlan.model_validate(entry)
        except Exception:
            section_plan[sec_id] = _fallback_section_plan(
                content_budget=content_budget,
                primary_caps=primary_caps,
                omit_caps=omit_caps,
                cert_names=relevant_certs,
            )[sec_id]

    # Ensure Who We Are never drops primary / never includes omit
    who = section_plan.get("section-1-who-we-are")
    if who is not None:
        included = [c for c in (who.included_capabilities or []) if c not in omit_caps]
        if not included:
            included = list(primary_caps)
        section_plan["section-1-who-we-are"] = who.model_copy(
            update={
                "included_capabilities": included,
                "omitted_capabilities": list(omit_caps),
            }
        )

    plan = Section1PlanResult(contentBudget=content_budget, sectionPlan=section_plan)
    return plan, plan_provider or budget_provider
