"""Proposal Context Agent — RFP classification only, no retrieval."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import ProposalContext
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

# Classification only — keep input/output small (cost).
_RFP_EXCERPT_CHARS = 12_000
_MAX_TOKENS = 1536


def _fallback_context(
    *,
    rfp_sector: str,
    raw: dict | None = None,
) -> ProposalContext:
    raw = raw or {}
    complexity = str(
        raw.get("projectComplexity") or raw.get("project_complexity") or "medium"
    ).lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    return ProposalContext(
        industry=str(raw.get("industry") or rfp_sector or "Other"),
        servicesRequested=[
            str(s)
            for s in (raw.get("servicesRequested") or raw.get("services_requested") or [])
            if str(s).strip()
        ],
        buyerType=str(raw.get("buyerType") or raw.get("buyer_type") or "Other"),
        evaluationPriorities=[
            str(s)
            for s in (
                raw.get("evaluationPriorities") or raw.get("evaluation_priorities") or []
            )
            if str(s).strip()
        ],
        projectComplexity=complexity,  # type: ignore[arg-type]
        proposalType=str(raw.get("proposalType") or raw.get("proposal_type") or "other"),
        summary=str(raw.get("summary") or "")[:500],
    )


async def run_proposal_context_agent(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> tuple[ProposalContext, str]:
    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Proposal Context Agent. Classify THIS RFP for downstream "
                        "capability prioritization and Section 1 composition.\n"
                        "Read ONLY the RFP excerpt — do NOT retrieve knowledge base content.\n"
                        "Do NOT write proposal prose. Return compact JSON only (no markdown fences).\n"
                        "Finish every string and brace — output must be valid JSON end-to-end.\n\n"
                        "{\n"
                        '  "industry": "string",\n'
                        '  "servicesRequested": ["short phrase", "..."],\n'
                        '  "buyerType": "Government|Nonprofit|Corporate|Other",\n'
                        '  "evaluationPriorities": ["short phrase"],\n'
                        '  "projectComplexity": "low|medium|high",\n'
                        '  "proposalType": "website_redesign|branding|campaign|other",\n'
                        '  "summary": "1-2 sentences only"\n'
                        "}\n"
                        "Keep lists ≤8 items. Keep summary under 40 words."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Title: {rfp_title}\n"
                        f"Client: {rfp_client}\n"
                        f"Sector: {rfp_sector}\n"
                        f"Location: {rfp_location or 'N/A'}\n\n"
                        f"RFP excerpt:\n{rfp_context[:_RFP_EXCERPT_CHARS]}"
                    ),
                },
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.1,
            tier="light",
        )
    except LlmError as exc:
        logger.warning(
            "Proposal Context LLM failed (%s); using sector fallback (no retry)",
            str(exc)[:180],
        )
        return _fallback_context(rfp_sector=rfp_sector), "fallback"

    try:
        context = ProposalContext.model_validate(raw)
    except Exception as exc:
        logger.warning("ProposalContext validation failed: %s", exc)
        context = _fallback_context(rfp_sector=rfp_sector, raw=raw if isinstance(raw, dict) else {})

    return context, provider
