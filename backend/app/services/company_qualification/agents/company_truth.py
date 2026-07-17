"""Company Truth Agent — JIT retrieval + structured fact extraction."""

from __future__ import annotations

import logging
from typing import Any

from app.services import llm
from app.services.company_qualification.retrieval.company_queries import (
    company_truth_extraction_schema,
    fetch_company_truth_corpus,
)
from app.services.company_qualification.schemas import CompanyTruth

logger = logging.getLogger(__name__)


async def run_company_truth_agent(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str,
) -> tuple[CompanyTruth, str]:
    corpus, sources = await fetch_company_truth_corpus(
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_context=rfp_context,
    )

    schema = company_truth_extraction_schema()
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Company Truth Agent for zö agency proposal Section 1.\n"
                    "Extract ONLY verified company facts from the knowledge base excerpts.\n"
                    "NEVER generate marketing copy. NEVER infer facts not in the excerpts.\n"
                    "NEVER include bio or case study content.\n"
                    "For missing fields use null or '[VERIFY: field name]' inside string values.\n"
                    f"Return JSON matching this schema exactly:\n{schema}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RFP client: {rfp_client}\n"
                    f"Sector: {rfp_sector}\n\n"
                    f"Company knowledge base excerpts:\n{corpus[:350000]}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.0,
    )

    if sources and not raw.get("sources"):
        raw["sources"] = sources[:20]

    try:
        truth = CompanyTruth.model_validate(raw)
    except Exception as exc:
        logger.warning("CompanyTruth validation failed, using partial: %s", exc)
        truth = CompanyTruth(sources=sources)

    if not truth.sources:
        truth = truth.model_copy(update={"sources": sources[:20]})

    return truth, provider
