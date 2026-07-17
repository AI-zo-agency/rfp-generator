"""Proposal Context Agent — RFP classification only, no retrieval."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import ProposalContext

logger = logging.getLogger(__name__)


async def run_proposal_context_agent(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> tuple[ProposalContext, str]:
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Proposal Context Agent. Classify THIS RFP for downstream "
                    "capability prioritization and Section 1 composition.\n"
                    "Read ONLY the RFP excerpt — do NOT retrieve knowledge base content.\n"
                    "Do NOT write proposal prose. Return classification JSON only.\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "industry": "string",\n'
                    '  "servicesRequested": ["string"],\n'
                    '  "buyerType": "Government|Nonprofit|Corporate|Other",\n'
                    '  "evaluationPriorities": ["string"],\n'
                    '  "projectComplexity": "low|medium|high",\n'
                    '  "proposalType": "website_redesign|branding|campaign|other",\n'
                    '  "summary": "one paragraph classification — not proposal copy"\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Title: {rfp_title}\n"
                    f"Client: {rfp_client}\n"
                    f"Sector: {rfp_sector}\n"
                    f"Location: {rfp_location or 'N/A'}\n\n"
                    f"RFP excerpt:\n{rfp_context[:50000]}"
                ),
            },
        ],
        max_tokens=1024,
        temperature=0.1,
    )

    try:
        context = ProposalContext.model_validate(raw)
    except Exception as exc:
        logger.warning("ProposalContext validation failed: %s", exc)
        context = ProposalContext(
            industry=rfp_sector,
            buyer_type="Other",
            summary=str(raw.get("summary") or "")[:500],
        )

    return context, provider
