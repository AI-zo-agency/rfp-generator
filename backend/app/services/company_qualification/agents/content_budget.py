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

logger = logging.getLogger(__name__)

DEFAULT_BUDGETS: list[SubsectionBudget] = [
    SubsectionBudget(
        sectionId="section-1-who-we-are",
        title="1.1 — Who We Are",
        format="narrative",
        wordMin=250,
        wordMax=350,
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


async def run_content_budget_agent(
    *,
    proposal_context: ProposalContext,
    prioritized_capabilities: PrioritizedCapabilities,
) -> tuple[Section1ContentBudget, str]:
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Content Budget Agent for zö agency Section 1.\n"
                    "Assign word/format budgets per subsection. Do NOT write proposal content.\n"
                    "Default budgets (adjust only if RFP clearly warrants it):\n"
                    "- 1.1 Who We Are: 250-350 words narrative\n"
                    "- 1.2 Org Structure: 400-900 words structured list — ALL roster people by department\n"
                    "- 1.3 Business Info: table format, no word target\n"
                    "- 1.4 Certifications: 75-150 words list\n"
                    "- 1.5 Insurance: 50-100 words facts\n\n"
                    "Return JSON:\n"
                    '{"budgets": [{"sectionId": "...", "title": "...", '
                    '"format": "narrative|table|list|facts", '
                    '"wordMin": int|null, "wordMax": int|null, "notes": "string|null"}]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RFP context:\n{proposal_context.model_dump_json()}\n\n"
                    f"Prioritized capabilities:\n{prioritized_capabilities.model_dump_json()}"
                ),
            },
        ],
        max_tokens=1024,
        temperature=0.0,
    )

    try:
        budget = Section1ContentBudget.model_validate(raw)
        if len(budget.budgets) != 5:
            raise ValueError(f"Expected 5 budgets, got {len(budget.budgets)}")
    except Exception as exc:
        logger.warning("Section1ContentBudget validation failed, using defaults: %s", exc)
        budget = Section1ContentBudget(budgets=list(DEFAULT_BUDGETS))

    return budget, provider
