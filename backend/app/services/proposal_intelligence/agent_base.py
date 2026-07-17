"""Shared helpers for Phase 2 intelligence agents."""

from __future__ import annotations

import logging
from typing import Any

from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_intelligence.schemas import DecisionLogEntry

logger = logging.getLogger(__name__)


def clamp_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def decision(
    agent: str,
    decision_text: str,
    reason: str,
    confidence: float,
) -> DecisionLogEntry:
    return DecisionLogEntry(
        agent=agent,
        decision=decision_text,
        reason=reason,
        confidence=clamp_confidence(confidence),
    )


async def safe_chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 3072,
    temperature: float = 0.15,
    agent_name: str = "agent",
) -> tuple[dict[str, Any], str]:
    """LLM JSON call that never raises — returns ({}, 'none') on failure."""
    try:
        raw, provider = await llm.chat_json(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if isinstance(raw, dict):
            return raw, provider
        logger.warning("%s returned non-dict JSON payload", agent_name)
        return {}, provider
    except LlmError as exc:
        logger.warning("%s LLM failed (non-fatal): %s", agent_name, str(exc)[:200])
        return {}, "none"
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s unexpected error (non-fatal): %s", agent_name, str(exc)[:200])
        return {}, "none"
