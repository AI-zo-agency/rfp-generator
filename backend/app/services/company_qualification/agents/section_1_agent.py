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

logger = logging.getLogger(__name__)

SECTION_IDS = (
    "section-1-who-we-are",
    "section-1-org-structure",
    "section-1-business-info",
    "section-1-certifications",
    "section-1-insurance",
)


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
    relevant_certs = [c.name for c in company_truth.certifications]

    raw, plan_provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Section 1 Agent for zö agency.\n"
                    "DECIDE what belongs in each Section 1 subsection — do NOT write prose.\n\n"
                    "Rules:\n"
                    "- Who We Are: primary capabilities only; never omit-tier; no client pitch\n"
                    "- Org Structure: include EVERY person from the Master Team Roster, grouped by "
                    "department (Leadership, Client Services, Project Management, Creative, "
                    "Finance/HR, coaches/implementors). Name + title for each. Never truncate to heads only.\n"
                    "- Business Info / Insurance: facts from CompanyTruth only\n"
                    "- Certifications: only certs relevant to this RFP context\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "sectionPlan": {\n'
                    '    "section-1-who-we-are": {\n'
                    '      "includedCapabilities": ["..."],\n'
                    '      "omittedCapabilities": ["..."],\n'
                    '      "targetWords": {"min": 180, "max": 250}\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                    "Include all 5 section ids in sectionPlan."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"ProposalContext:\n{proposal_context.model_dump_json()}\n\n"
                    f"Primary capabilities: {primary_caps}\n"
                    f"Secondary: {secondary_caps}\n"
                    f"Omit (must NOT appear in 1.1): {omit_caps}\n\n"
                    f"Available certifications: {relevant_certs}\n\n"
                    f"Content budgets:\n{content_budget.model_dump_json()}"
                ),
            },
        ],
        max_tokens=2048,
        temperature=0.0,
    )

    section_plan_raw = raw.get("sectionPlan") or raw.get("section_plan") or {}
    section_plan: dict[str, SubsectionPlan] = {}
    for sec_id in SECTION_IDS:
        entry = section_plan_raw.get(sec_id) or {}
        try:
            section_plan[sec_id] = SubsectionPlan.model_validate(entry)
        except Exception:
            b = next((x for x in content_budget.budgets if x.section_id == sec_id), None)
            section_plan[sec_id] = SubsectionPlan(
                includedCapabilities=primary_caps if sec_id == "section-1-who-we-are" else [],
                omittedCapabilities=omit_caps if sec_id == "section-1-who-we-are" else [],
                targetWords={
                    "min": b.word_min if b else None,
                    "max": b.word_max if b else None,
                },
            )

    plan = Section1PlanResult(contentBudget=content_budget, sectionPlan=section_plan)
    return plan, plan_provider or budget_provider
