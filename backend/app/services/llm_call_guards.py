"""Hard preflight guards so accidental/ephemeral LLM spend cannot hit providers.

Blocks:
* demo- / fixture- / rfp-test* RFP ids (the burn that showed up as ``demo-*``
  opportunity_extract rows with no matching RFP in this project's table)
* proposal LLM calls with no signed-in ``user_email`` (anonymous scripts /
  foreign workers that share Supabase keys but skip the browser header)

Finance nodes are exempt from the email rule (nightly insights have no login).
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_BLOCKED_RFP_PREFIXES: tuple[str, ...] = (
    "demo-",
    "fixture-",
    "rfp-test",
    "test-rfp",
    "test-",
)

_BLOCKED_RFP_EXACT: frozenset[str] = frozenset(
    {
        "rfp-test",
        "rfp-test-2",
        "fixture-cvvb-v2-rfp",
    }
)

# Explicit extract-family nodes that have been burning OpenRouter on demos.
_BLOCKED_NODE_SUBSTRINGS: tuple[str, ...] = (
    "opportunity_extract_repair",
    "opportunity_extract_a",
    "opportunity_extract_b",
    "opportunity_extract_c",
    "opportunity_extract_qa",
)


def is_blocked_rfp_id(rfp_id: str | None) -> bool:
    rid = (rfp_id or "").strip().lower()
    if not rid:
        return False
    if rid in _BLOCKED_RFP_EXACT:
        return True
    return any(rid.startswith(prefix) for prefix in _BLOCKED_RFP_PREFIXES)


def _is_financial_node(node_name: str) -> bool:
    try:
        from app.services.llm import _is_financial_node

        return bool(_is_financial_node(node_name))
    except Exception:  # noqa: BLE001
        n = (node_name or "").strip().lower()
        return n.startswith("financial.") or "insights" in n


def _node_explicitly_blocked(node_name: str) -> bool:
    n = (node_name or "").strip().lower()
    if not n:
        return False
    # Always allow the real pipeline opportunity_extract for signed-in real RFPs;
    # only the parallel/repair variants that burned demo spend are hard-banned.
    return any(s in n for s in _BLOCKED_NODE_SUBSTRINGS)


def enforce_llm_call_guards() -> None:
    """Raise ``LlmError(403)`` when this call must not reach a provider."""
    from app.services.llm import LlmError
    from app.services.llm_call_context import (
        get_llm_node_name,
        get_llm_rfp_id,
        get_llm_user_email,
    )

    rfp_id = get_llm_rfp_id()
    node_name = get_llm_node_name()
    user_email = get_llm_user_email()

    if _is_financial_node(node_name):
        return

    if bool(getattr(settings, "llm_block_ephemeral_rfp_ids", True)) and is_blocked_rfp_id(
        rfp_id
    ):
        logger.warning(
            "Blocked LLM call for ephemeral rfp_id=%s node=%s",
            rfp_id,
            node_name,
        )
        raise LlmError(
            (
                f"LLM blocked for ephemeral/test RFP id {rfp_id!r}. "
                "demo-/fixture-/rfp-test* ids cannot spend OpenRouter."
            ),
            status_code=403,
        )

    if _node_explicitly_blocked(node_name):
        logger.warning("Blocked LLM call for banned node=%s rfp=%s", node_name, rfp_id)
        raise LlmError(
            (
                f"LLM blocked for node {node_name!r} — parallel extract/repair "
                "paths are disabled to stop unnecessary spend."
            ),
            status_code=403,
        )

    require_email = bool(
        getattr(settings, "llm_require_user_email_for_proposals", True)
    )
    # Only enforce when we have a proposal rfp context (or a pipeline node).
    # Bare helper calls with empty rfp + empty node stay allowed for unit tests
    # that never set context — but real pipeline always sets both.
    if require_email and not user_email and (rfp_id or node_name):
        logger.warning(
            "Blocked anonymous proposal LLM rfp=%s node=%s",
            rfp_id,
            node_name,
        )
        raise LlmError(
            (
                "Proposal LLM requires a signed-in user email "
                "(X-User-Email). Anonymous / scripted runs are blocked."
            ),
            status_code=403,
        )
