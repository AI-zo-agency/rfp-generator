"""Company Truth Agent — JIT retrieval + structured fact extraction."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.retrieval.company_queries import (
    company_truth_extraction_schema,
    fetch_company_truth_corpus,
)
from app.services.company_qualification.schemas import CompanyTruth
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

# Single shot only — sized so Sonnet finishes COMPLETE JSON (no retry loop = no cost spiral).
_CORPUS_CHARS = 28_000
_MAX_TOKENS = 8192


def _require_complete_company_truth(truth: CompanyTruth) -> None:
    if not (truth.legal_name or "").strip():
        raise LlmError(
            "Company Truth incomplete: missing legalName.",
            status_code=422,
        )
    if not (truth.dba or truth.founded or truth.ownership):
        raise LlmError(
            "Company Truth incomplete: need dba/founded/ownership.",
            status_code=422,
        )
    has_contact = bool(
        truth.contact
        and (truth.contact.phone or truth.contact.email or truth.contact.website)
    )
    has_location = bool(
        truth.locations
        and (truth.locations.office or truth.locations.mailing or truth.locations.remittance)
    )
    has_caps = bool(truth.capabilities)
    has_regs = bool(
        truth.business_registration
        and (truth.business_registration.ein or truth.business_registration.state_ids)
    )
    if not (has_contact or has_location or has_caps or has_regs):
        raise LlmError(
            "Company Truth incomplete: need contact, location, capabilities, or registration.",
            status_code=422,
        )


async def run_company_truth_agent(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str,
) -> tuple[CompanyTruth, str]:
    """One LLM call. Complete CompanyTruth or fail — no retries, no partial stub."""
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
                    "NEVER generate marketing copy. NEVER invent facts not in the excerpts.\n"
                    "NEVER include bio or case study prose.\n"
                    "Return ONE complete JSON object (no markdown fences). "
                    "Finish every string and brace — output must be valid JSON end-to-end.\n"
                    "Keep lists short: ≤10 capabilities, ≤6 certs, ≤5 departments, ≤5 insurance.\n"
                    "Missing facts → null.\n"
                    f"Schema:\n{schema}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RFP client: {rfp_client}\n"
                    f"Sector: {rfp_sector}\n\n"
                    f"Company knowledge base excerpts:\n{corpus[:_CORPUS_CHARS]}"
                ),
            },
        ],
        max_tokens=_MAX_TOKENS,
        temperature=0.0,
        tier="heavy",
    )

    if sources and not raw.get("sources"):
        raw = {**raw, "sources": sources[:20]}

    try:
        truth = CompanyTruth.model_validate(raw)
    except Exception as exc:
        raise LlmError(
            f"Company Truth JSON did not match schema: {exc}",
            status_code=422,
        ) from exc

    if not truth.sources:
        truth = truth.model_copy(update={"sources": sources[:20]})

    _require_complete_company_truth(truth)
    logger.info(
        "Company Truth ok (legal=%s, caps=%d, sources=%d)",
        truth.legal_name,
        len(truth.capabilities),
        len(truth.sources),
    )
    return truth, provider
