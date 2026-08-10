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

# ---------------------------------------------------------------------------
# Explicit per-stage model map.
#
# Every LLM call in the proposal pipeline is either QUALITY (it writes prose a
# client will read, or makes a judgment about correctness) or MECHANICAL (it
# selects, plans, classifies, or reshapes — a cheap model is genuinely fine).
#
# An UNKNOWN node name resolves to QUALITY and logs a warning. That direction
# matters: the previous behaviour defaulted unknown nodes to mechanical, so
# every repair agent, the [VERIFY] scrubber and the KB fact-checker silently
# ran on the cheapest model in the stack while the drafter used a better one —
# judgment was being done by a weaker model than composition.
# ---------------------------------------------------------------------------

# Mechanical: selection, planning, classification, structural reshaping.
# These pick, order or route — they do not compose client-facing prose and do
# not judge whether a factual claim is supported.
_MECHANICAL_EXACT = frozenset(
    {
        # Sections 1-3 graph planners / selectors.
        "plan_section_1",
        "prioritize_capabilities",
        "select_team",
        "select_evidence",
        "fetch_proposal_context",
        "fetch_knowledge_base",
        # Chat + edit planners.
        "query_planner",
        "section_dedup",
        "team_select",
        "case_select",
        "brand_voice",
        "manuscript_locks",
        "chat_edit_scope_plan",
        "chat_structure_plan",
        "chat_structure_split",
        "chat_manuscript_intent",
        "chat_manuscript_fix_plan",
        "fee_slot_fill_plan",
        # Phase-2 intelligence planners.
        "retrieval_planner",
        "dynamic_section",
        "section_strategy",
        # Requirement-ledger ADD (Task 10) — plans KB queries for a
        # newly-added section stub before drafting it.
        "ledger_add_query_planner",
    }
)

# Quality: prose a client reads, or a correctness judgment about that prose.
_QUALITY_EXACT = frozenset(
    {
        "fetch_company_truth",
        "build_case_studies",
        "build_section_1_cq",
        "build_section_1",
        "manuscript_auditor",
        "senior_editor",
        # Judgment stages that previously fell through to the cheapest model.
        "section_repair",
        "user_revise",
        "surgical_fix",
        "verify_optional_scrub",
        "kb_fact_check",
        "rfp_structure_reframe",
        "bio_extract",
        "budget_claim_grounding_check",
        "stage35a_budget_grounding",
        "money_intelligence_pass_a",
        "money_intelligence_pass_b",
        "rfp_understanding",
        "capability_adjudicator",
        # Picks the KB queries behind an excerpt revision — if it targets the
        # wrong facts, the rewriter has nothing true to say.
        "chat_selection_kb_plan",
        # Requirement-ledger ADD (Task 10) — drafts client-facing prose for a
        # section that had no draft content at all; a client reads this.
        "ledger_add_section_draft",
        # Scan-RFP truncation repair (Task 12) — completes a section a
        # client reads that was cut off mid-sentence; grounded on KB
        # evidence, so getting the completion right matters the same way
        # ledger_add_section_draft does.
        "scan_truncation_kb_repair",
    }
)
_QUALITY_PREFIXES = (
    "draft_sections",
    "build_case_studies",
    "build_section_1",
    "chat_full_redraft",
    "chat_excerpt_edit",
    "chat_replace_section",
    "chat_manuscript_surgical_patch",
)

_warned_unknown_nodes: set[str] = set()


@dataclass(frozen=True)
class FireworksEligibility:
    allow_fireworks: bool
    skip_prefer_fireworks: bool
    must_raise: bool
    effective_cap_if_fireworks: int
    requested_max_tokens: int
    block_reason: str | None = None


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        name == p or name.startswith(f"{p}:") or name.startswith(f"{p}_")
        for p in prefixes
    )


def classify_node(node_name: str | None) -> str:
    """Return "quality" or "mechanical" for a pipeline node.

    Unknown / unnamed nodes resolve to "quality" and warn once, so a new call
    site that forgets its node_name fails toward correctness instead of being
    silently served by the cheapest provider.
    """
    name = (node_name or "").strip()
    if not name:
        return "quality"
    if name in _MECHANICAL_EXACT:
        return "mechanical"
    if name in _QUALITY_EXACT or _matches(name, _QUALITY_PREFIXES):
        return "quality"
    if name not in _warned_unknown_nodes:
        _warned_unknown_nodes.add(name)
        logger.warning(
            "llm_stage_map unknown_node=%s — defaulting to quality tier. "
            "Add it to _QUALITY_EXACT or _MECHANICAL_EXACT in llm_routing.py.",
            name,
        )
    return "quality"


def is_quality_critical_node(node_name: str | None) -> bool:
    """True when this node must not be forced onto the cheapest provider."""
    return classify_node(node_name) == "quality"


def resolve_fireworks_eligibility(
    *,
    requested_max_tokens: int | None,
    prefer_fireworks: bool,
    node_name: str | None,
    openrouter_available: bool,
    gemini_available: bool,
    disable_fireworks: bool = False,
) -> FireworksEligibility:
    """Decide whether Fireworks may serve this call without silent under-serve."""
    requested = int(requested_max_tokens or DEFAULT_REQUESTED_MAX_TOKENS)
    if requested < 1:
        requested = DEFAULT_REQUESTED_MAX_TOKENS

    if disable_fireworks:
        logger.info(
            "fireworks_routing disabled node=%s requested=%s",
            node_name or "unknown",
            requested,
        )
        return FireworksEligibility(
            allow_fireworks=False,
            skip_prefer_fireworks=True,
            must_raise=False,
            effective_cap_if_fireworks=FIREWORKS_OUTPUT_TOKEN_CAP,
            requested_max_tokens=requested,
            block_reason="Fireworks disabled via LLM_DISABLE_FIREWORKS",
        )

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
