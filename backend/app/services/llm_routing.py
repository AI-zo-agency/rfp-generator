"""Cap-aware LLM provider routing (T3.1) and quality-critical preference (T3.2).

Fireworks hard-caps output at 8192. Callers that request more must not be silently
served with min(requested, 8192). Quality-critical writing nodes ignore
LLM_PREFER_FIREWORKS and resolve by normal tier/provider order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

FIREWORKS_OUTPUT_TOKEN_CAP = 8192
DEFAULT_REQUESTED_MAX_TOKENS = 4096

# Prefixes / exact names that must not be forced onto Fireworks via prefer flag.
_QUALITY_CRITICAL_EXACT = frozenset(
    {
        "fetch_company_truth",
        "build_case_studies",
        "build_section_1_cq",
        "build_section_1",
        "manuscript_auditor",
        "senior_editor",
    }
)
_QUALITY_CRITICAL_PREFIXES = (
    "draft_sections",
    "build_case_studies",
    "build_section_1",
)


@dataclass(frozen=True)
class FireworksEligibility:
    allow_fireworks: bool
    skip_prefer_fireworks: bool
    must_raise: bool
    effective_cap_if_fireworks: int
    requested_max_tokens: int
    block_reason: str | None = None


def is_quality_critical_node(node_name: str | None) -> bool:
    """True for drafting / company-truth / case-study / auditor-class nodes."""
    name = (node_name or "").strip()
    if not name:
        return False
    if name in _QUALITY_CRITICAL_EXACT:
        return True
    return any(name == p or name.startswith(f"{p}:") or name.startswith(f"{p}_") for p in _QUALITY_CRITICAL_PREFIXES)


def resolve_fireworks_eligibility(
    *,
    requested_max_tokens: int | None,
    prefer_fireworks: bool,
    node_name: str | None,
    openrouter_available: bool,
    gemini_available: bool,
) -> FireworksEligibility:
    """Decide whether Fireworks may serve this call without silent under-serve."""
    requested = int(requested_max_tokens or DEFAULT_REQUESTED_MAX_TOKENS)
    if requested < 1:
        requested = DEFAULT_REQUESTED_MAX_TOKENS

    quality_critical = is_quality_critical_node(node_name)
    skip_prefer = quality_critical and prefer_fireworks
    over_cap = requested > FIREWORKS_OUTPUT_TOKEN_CAP
    has_alt = openrouter_available or gemini_available

    if over_cap:
        reason = (
            f"requested max_tokens={requested} exceeds Fireworks output cap "
            f"{FIREWORKS_OUTPUT_TOKEN_CAP}"
        )
        if has_alt:
            logger.info(
                "fireworks_routing skip_underserve node=%s requested=%s cap=%s "
                "openrouter=%s gemini=%s",
                node_name or "unknown",
                requested,
                FIREWORKS_OUTPUT_TOKEN_CAP,
                openrouter_available,
                gemini_available,
            )
            return FireworksEligibility(
                allow_fireworks=False,
                skip_prefer_fireworks=skip_prefer or True,
                must_raise=False,
                effective_cap_if_fireworks=FIREWORKS_OUTPUT_TOKEN_CAP,
                requested_max_tokens=requested,
                block_reason=reason + " — routing to alternative provider",
            )
        logger.error(
            "fireworks_routing no_alternative node=%s requested=%s cap=%s",
            node_name or "unknown",
            requested,
            FIREWORKS_OUTPUT_TOKEN_CAP,
        )
        return FireworksEligibility(
            allow_fireworks=False,
            skip_prefer_fireworks=True,
            must_raise=True,
            effective_cap_if_fireworks=FIREWORKS_OUTPUT_TOKEN_CAP,
            requested_max_tokens=requested,
            block_reason=reason + " and no alternative provider configured",
        )

    if skip_prefer:
        logger.info(
            "fireworks_routing skip_prefer_quality_critical node=%s requested=%s",
            node_name or "unknown",
            requested,
        )

    return FireworksEligibility(
        allow_fireworks=True,
        skip_prefer_fireworks=skip_prefer,
        must_raise=False,
        effective_cap_if_fireworks=FIREWORKS_OUTPUT_TOKEN_CAP,
        requested_max_tokens=requested,
        block_reason=None,
    )
