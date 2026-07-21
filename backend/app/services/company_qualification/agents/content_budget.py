"""Content Budget Agent — explicit per-subsection word/format budgets."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import (
    PrioritizedCapabilities,
    ProposalContext,
    Section1ContentBudget,
    SubsectionBudget,
)
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

DEFAULT_BUDGETS: list[SubsectionBudget] = [
    SubsectionBudget(
        sectionId="section-1-who-we-are",
        title="1.1 — Who We Are",
        format="narrative",
        wordMin=180,
        wordMax=250,
    ),
    SubsectionBudget(
        sectionId="section-1-org-structure",
        title="1.2 — Organizational Structure",
        format="list",
        wordMin=400,
        wordMax=900,
        notes="Full org by department: list ALL people from Master Team Roster with name + title",
    ),
    SubsectionBudget(
        sectionId="section-1-business-info",
        title="1.3 — Business Information",
        format="table",
        wordMin=None,
        wordMax=None,
        notes="Mostly table; facts only, no narrative",
    ),
    SubsectionBudget(
        sectionId="section-1-certifications",
        title="1.4 — Certifications",
        format="list",
        wordMin=75,
        wordMax=150,
    ),
    SubsectionBudget(
        sectionId="section-1-insurance",
        title="1.5 — Insurance Information",
        format="facts",
        wordMin=50,
        wordMax=100,
    ),
]

_CANONICAL_IDS = {b.section_id for b in DEFAULT_BUDGETS}


def _normalize_budget_ids(budget: Section1ContentBudget) -> Section1ContentBudget | None:
    """Map LLM sectionId variants (1.1, Who We Are, …) onto canonical ids."""
    by_title = {b.title.casefold(): b.section_id for b in DEFAULT_BUDGETS}
    aliases = {
        "1.1": "section-1-who-we-are",
        "1.2": "section-1-org-structure",
        "1.3": "section-1-business-info",
        "1.4": "section-1-certifications",
        "1.5": "section-1-insurance",
        "who we are": "section-1-who-we-are",
        "organizational structure": "section-1-org-structure",
        "org structure": "section-1-org-structure",
        "business information": "section-1-business-info",
        "business info": "section-1-business-info",
        "certifications": "section-1-certifications",
        "insurance information": "section-1-insurance",
        "insurance": "section-1-insurance",
    }
    fixed: list[SubsectionBudget] = []
    seen: set[str] = set()
    for item in budget.budgets:
        sid = (item.section_id or "").strip()
        if sid not in _CANONICAL_IDS:
            key = sid.casefold()
            title_key = (item.title or "").casefold()
            sid = (
                aliases.get(key)
                or aliases.get(title_key)
                or by_title.get(title_key)
                or sid
            )
        if sid not in _CANONICAL_IDS or sid in seen:
            continue
        seen.add(sid)
        fixed.append(item.model_copy(update={"section_id": sid}))
    if len(fixed) != 5:
        return None
    return Section1ContentBudget(budgets=fixed)


async def run_content_budget_agent(
    *,
    proposal_context: ProposalContext,
    prioritized_capabilities: PrioritizedCapabilities,
) -> tuple[Section1ContentBudget, str]:
    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Content Budget Agent for zö agency Section 1.\n"
                        "Assign word/format budgets per subsection. Do NOT write proposal content.\n"
                        "Use EXACT sectionId values below. Keep notes ≤6 words or null.\n"
                        "Compact JSON only — no markdown fences.\n"
                        "Defaults (tweak only if RFP clearly needs it):\n"
                        "- section-1-who-we-are: narrative 180-250\n"
                        "- section-1-org-structure: list 400-900\n"
                        "- section-1-business-info: table, wordMin/wordMax null\n"
                        "- section-1-certifications: list 75-150\n"
                        "- section-1-insurance: facts 50-100\n"
                        'Return: {"budgets":[{"sectionId":"…","title":"…","format":"narrative|table|list|facts",'
                        '"wordMin":int|null,"wordMax":int|null,"notes":null}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n"
                        f"servicesRequested: {proposal_context.services_requested}\n"
                        f"primaryCaps: {[c.capability for c in prioritized_capabilities.primary][:5]}"
                    ),
                },
            ],
            max_tokens=1536,
            temperature=0.0,
            tier="light",
        )
    except LlmError as exc:
        logger.warning(
            "Content budget LLM failed (%s); using defaults (no retry)",
            str(exc)[:180],
        )
        return Section1ContentBudget(budgets=list(DEFAULT_BUDGETS)), "defaults"

    try:
        budget = Section1ContentBudget.model_validate(raw)
        normalized = _normalize_budget_ids(budget)
        if normalized is None:
            raise ValueError(f"Expected 5 canonical budgets, got {len(budget.budgets)}")
        return normalized, provider
    except Exception as exc:
        logger.warning("Section1ContentBudget validation failed, using defaults: %s", exc)
        return Section1ContentBudget(budgets=list(DEFAULT_BUDGETS)), "defaults"
