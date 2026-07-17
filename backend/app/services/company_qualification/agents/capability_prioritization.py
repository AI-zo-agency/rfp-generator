"""Capability Prioritization Agent — Primary / Secondary / Omit tiers."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import CapabilityTierItem, CompanyTruth, PrioritizedCapabilities, ProposalContext

logger = logging.getLogger(__name__)


async def run_capability_prioritization_agent(
    *,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
) -> tuple[PrioritizedCapabilities, str]:
    capabilities = company_truth.capabilities or []
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Capability Prioritization Agent for zö agency Section 1.\n"
                    "Given company capabilities and RFP context, decide what belongs in "
                    "Section 1 company qualification content.\n"
                    "Do NOT retrieve additional content. Do NOT write proposal prose.\n\n"
                    "Return JSON with three tiers:\n"
                    "{\n"
                    '  "primary": [{"capability": "string", "rationale": "string"}],\n'
                    '  "secondary": [{"capability": "string", "rationale": "string"}],\n'
                    '  "omit": [{"capability": "string", "rationale": "string"}]\n'
                    "}\n\n"
                    "Primary = lead with these in Who We Are. Secondary = mention briefly if space. "
                    "Omit = must NOT appear in Section 1."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RFP context:\n{proposal_context.model_dump_json()}\n\n"
                    f"Company capabilities from verified truth:\n{capabilities}"
                ),
            },
        ],
        max_tokens=2048,
        temperature=0.1,
    )

    try:
        prioritized = PrioritizedCapabilities.model_validate(raw)
    except Exception as exc:
        logger.warning("PrioritizedCapabilities validation failed: %s", exc)
        prioritized = PrioritizedCapabilities(
            primary=[
                CapabilityTierItem(capability=c, rationale="Listed company capability")
                for c in capabilities[:3]
            ],
            omit=[
                CapabilityTierItem(capability=c, rationale="Lower priority for this RFP")
                for c in capabilities[3:]
            ],
        )

    return prioritized, provider
