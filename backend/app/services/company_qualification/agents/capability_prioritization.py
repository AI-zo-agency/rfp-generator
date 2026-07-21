"""Capability Prioritization Agent — Primary / Secondary / Omit tiers."""

from __future__ import annotations

import logging
import re

from app.services import llm
from app.services.company_qualification.schemas import (
    CapabilityTierItem,
    CompanyTruth,
    PrioritizedCapabilities,
    ProposalContext,
)
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

_MAX_TOKENS = 1024


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _heuristic_prioritize(
    capabilities: list[str],
    proposal_context: ProposalContext,
) -> PrioritizedCapabilities:
    """Zero-cost fallback — keyword overlap with RFP services/summary. No LLM."""
    needles = set()
    for item in proposal_context.services_requested or []:
        needles |= _tokenize(str(item))
    needles |= _tokenize(proposal_context.summary or "")
    needles |= _tokenize(proposal_context.proposal_type or "")
    needles |= _tokenize(proposal_context.industry or "")

    scored: list[tuple[int, str]] = []
    for cap in capabilities:
        tokens = _tokenize(cap)
        score = len(tokens & needles)
        # Mild boost for web/digital when proposal type implies redesign.
        ptype = (proposal_context.proposal_type or "").lower()
        if "website" in ptype or "redesign" in ptype or "digital" in ptype:
            if tokens & {"web", "website", "digital", "development", "ux", "ui", "design"}:
                score += 2
        scored.append((score, cap))

    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    primary = [
        CapabilityTierItem(capability=c, rationale="RFP service overlap")
        for _, c in scored[:3]
    ]
    secondary = [
        CapabilityTierItem(capability=c, rationale="Supporting capability")
        for _, c in scored[3:6]
    ]
    omit = [
        CapabilityTierItem(capability=c, rationale="Lower priority for this RFP")
        for _, c in scored[6:]
    ]
    return PrioritizedCapabilities(primary=primary, secondary=secondary, omit=omit)


async def run_capability_prioritization_agent(
    *,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
) -> tuple[PrioritizedCapabilities, str]:
    capabilities = [c for c in (company_truth.capabilities or []) if str(c).strip()]
    if not capabilities:
        return PrioritizedCapabilities(), "none"

    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Capability Prioritization Agent for zö agency Section 1.\n"
                        "Rank the given capabilities into primary / secondary / omit for THIS RFP.\n"
                        "Do NOT retrieve content. Do NOT write proposal prose.\n"
                        "Return compact JSON only — no markdown fences.\n"
                        "Every capability string must appear in exactly one tier.\n"
                        "Rationale ≤8 words each.\n"
                        "Schema:\n"
                        '{"primary":[{"capability":"…","rationale":"…"}],'
                        '"secondary":[…],"omit":[…]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n"
                        f"servicesRequested: {proposal_context.services_requested}\n"
                        f"summary: {(proposal_context.summary or '')[:280]}\n\n"
                        f"capabilities: {capabilities}"
                    ),
                },
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.0,
            tier="light",
        )
    except LlmError as exc:
        # Already spent one call (or provider failed) — do not re-call; keep pipeline moving.
        logger.warning(
            "Capability prioritization LLM failed (%s); using heuristic (no retry)",
            str(exc)[:180],
        )
        return _heuristic_prioritize(capabilities, proposal_context), "heuristic"

    try:
        prioritized = PrioritizedCapabilities.model_validate(raw)
    except Exception as exc:
        logger.warning(
            "PrioritizedCapabilities validation failed: %s; using heuristic",
            exc,
        )
        return _heuristic_prioritize(capabilities, proposal_context), "heuristic"

    placed = {
        (item.capability or "").strip().casefold()
        for bucket in (prioritized.primary, prioritized.secondary, prioritized.omit)
        for item in bucket
        if (item.capability or "").strip()
    }
    missing = [c for c in capabilities if c.strip().casefold() not in placed]
    if missing:
        prioritized = prioritized.model_copy(
            update={
                "omit": list(prioritized.omit)
                + [
                    CapabilityTierItem(capability=c, rationale="Unassigned by model")
                    for c in missing
                ]
            }
        )

    if not prioritized.primary and not prioritized.secondary:
        return _heuristic_prioritize(capabilities, proposal_context), "heuristic"

    return prioritized, provider
