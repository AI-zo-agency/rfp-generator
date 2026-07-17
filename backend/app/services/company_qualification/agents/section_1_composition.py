"""Section 1 Composition Agent — only node that produces human-readable Section 1 prose."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services import llm
from app.services.company_qualification.schemas import (
    CompanyTruth,
    GeneratedSubsection,
    PrioritizedCapabilities,
    ProposalContext,
    Section1CompositionResult,
    Section1ContentBudget,
    SubsectionPlan,
)

logger = logging.getLogger(__name__)

SECTION_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "section-1-who-we-are",
        "1.1 — Who We Are",
        "Structure: ## Who We Are and ## Our Promise. Lead with primary capabilities only. "
        "No client-specific pitch. No omit-tier capabilities. No certifications or insurance.",
    ),
    (
        "section-1-org-structure",
        "1.2 — Organizational Structure",
        "List the COMPLETE organizational structure from the Master Team Roster. "
        "Group by department. For EVERY person: Name — Title. Never omit roster members. "
        "Never invent people. Never full resumes.",
    ),
    (
        "section-1-business-info",
        "1.3 — Business Information",
        "Facts/table only: legal name, DBA, ownership, EIN, registration, addresses, contact. "
        "NO narrative, NO Who We Are copy, NO certifications, NO insurance.",
    ),
    (
        "section-1-certifications",
        "1.4 — Certifications",
        "Filter certifications relevant to RFP context. Agency certs only (WBENC, WOSB). "
        "No platform/individual certs.",
    ),
    (
        "section-1-insurance",
        "1.5 — Insurance Information",
        "Coverage types from company truth. Use [VERIFY: amount] for unknown dollar figures.",
    ),
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _budget_for(budgets: Section1ContentBudget, section_id: str) -> dict[str, Any]:
    for b in budgets.budgets:
        if b.section_id == section_id:
            return {
                "format": b.format,
                "wordMin": b.word_min,
                "wordMax": b.word_max,
                "notes": b.notes,
            }
    return {}


async def run_section_1_composition_agent(
    *,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
    prioritized_capabilities: PrioritizedCapabilities,
    content_budget: Section1ContentBudget,
    brand_voice_block: str,
    rfp_client: str,
    rfp_sector: str,
    master_roster_excerpt: str = "",
) -> tuple[Section1CompositionResult, str]:
    primary_caps = [c.capability for c in prioritized_capabilities.primary]
    omit_caps = [c.capability for c in prioritized_capabilities.omit]

    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Section 1 Composition Agent for zö agency.\n"
                    "You are the ONLY agent that writes human-readable proposal content for Section 1.\n"
                    "Use ONLY facts from CompanyTruth JSON. Do NOT invent facts.\n"
                    "Apply content budgets strictly. Respect capability tiers — omit-tier must not appear.\n"
                    "Write in first person (we/our/us). Never use 'The Vendor'.\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "sectionPlan": {\n'
                    '    "section-1-who-we-are": {\n'
                    '      "includedCapabilities": ["..."],\n'
                    '      "omittedCapabilities": ["..."],\n'
                    '      "targetWords": {"min": 250, "max": 350}\n'
                    "    }\n"
                    "  },\n"
                    '  "generatedSections": [\n'
                    '    {"id": "section-1-who-we-are", "title": "1.1 — Who We Are", '
                    '"content": "markdown", "wordCount": 0}\n'
                    "  ]\n"
                    "}\n\n"
                    "Generate all 5 subsections in generatedSections."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Voice:\n{brand_voice_block}\n\n"
                    f"Client: {rfp_client} | Sector: {rfp_sector}\n\n"
                    f"CompanyTruth:\n{company_truth.model_dump_json()}\n\n"
                    f"ProposalContext:\n{proposal_context.model_dump_json()}\n\n"
                    f"Primary capabilities: {primary_caps}\n"
                    f"Omit capabilities (must NOT appear): {omit_caps}\n\n"
                    f"Content budgets:\n{content_budget.model_dump_json()}\n\n"
                    f"Subsection specs:\n{SECTION_SPECS}\n\n"
                    + (
                        f"Master team roster (org structure reference only):\n"
                        f"{master_roster_excerpt[:120000]}\n"
                        if master_roster_excerpt
                        else ""
                    )
                ),
            },
        ],
        max_tokens=8192,
        temperature=0.2,
    )

    try:
        result = Section1CompositionResult.model_validate(raw)
    except Exception as exc:
        logger.warning("Section1CompositionResult validation failed: %s", exc)
        result = Section1CompositionResult()

    # Ensure all 5 sections present with word counts
    by_id = {s.id: s for s in result.generated_sections}
    fixed_sections: list[GeneratedSubsection] = []
    section_plan: dict[str, SubsectionPlan] = dict(result.section_plan)

    for sec_id, title, _hint in SECTION_SPECS:
        sec = by_id.get(sec_id)
        if not sec:
            sec = GeneratedSubsection(id=sec_id, title=title, content="")
        wc = _word_count(sec.content)
        fixed_sections.append(sec.model_copy(update={"wordCount": wc}))

        if sec_id not in section_plan:
            b = _budget_for(content_budget, sec_id)
            section_plan[sec_id] = SubsectionPlan(
                includedCapabilities=primary_caps if sec_id == "section-1-who-we-are" else [],
                omittedCapabilities=omit_caps if sec_id == "section-1-who-we-are" else [],
                targetWords={"min": b.get("wordMin"), "max": b.get("wordMax")},
            )

    result = Section1CompositionResult(
        sectionPlan=section_plan,
        generatedSections=fixed_sections,
    )
    return result, provider
