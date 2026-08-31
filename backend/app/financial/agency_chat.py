"""Grounded interactive answers for the Agency weekly intelligence drawer."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.core.config import settings
from app.financial import financial_llm_cost
from app.financial.agency_insights import _SYSTEM, build_evidence
from app.financial.figure_guard import check_magnitude_claims, check_quantities, evidence_numbers
from app.services.llm import LlmError, chat_text

logger = logging.getLogger(__name__)

_MAX_TOKENS = 4096
_TEMPERATURE = 0.2
MAX_HISTORY_TURNS = 8
MAX_QUESTION_CHARS = 2000
_NODE = "agency_chat.answer"

GUARDED_REPLY = (
    "I can't answer that from the Agency evidence I have without stating a figure "
    "it doesn't back. Try asking about a carryover item, mapping gap, or queue row."
)
BUDGET_REPLY = "This conversation has reached its spending limit. Start a new one to keep going."

_CHAT_RULES = (
    "\n\nYou answer an owner's question about the Agency join layer, not a daily brief. "
    "Use two to four sentences with no heading or list. The evidence is everything you "
    "know. Reuse supplied figures exactly or omit them. Never claim payment was collected, "
    "never invent team members or past deliveries, and never restate full QuickBooks or "
    "Teamwork dashboards."
)


def _unsupported_figure(text: str, allowed: set[float]) -> str | None:
    return check_quantities(text, allowed) or check_magnitude_claims(text)


def _trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    clean = [
        {"role": message["role"], "content": message["content"].strip()}
        for message in history
        if message.get("role") in ("user", "assistant") and (message.get("content") or "").strip()
    ]
    return clean[-(MAX_HISTORY_TURNS * 2) :]


def _focus_signal(evidence: dict[str, Any], focus_id: str | None) -> dict[str, Any] | None:
    if not focus_id:
        return None
    return next(
        (row for row in evidence.get("signals") or [] if row.get("id") == focus_id),
        None,
    )


def build_chat_messages(
    evidence: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    focus_id: str | None = None,
) -> list[dict[str, str]]:
    messages = [{
        "role": "system",
        "content": f"{_SYSTEM}{_CHAT_RULES}\n\nCurrent Agency evidence:\n\n{json.dumps(evidence, indent=2)}",
    }]
    messages.extend(_trim_history(history))
    focus = _focus_signal(evidence, focus_id)
    prompt = question.strip()
    if focus:
        prompt = f"{prompt}\n\nPinned signal: {json.dumps(focus)}"
    messages.append({"role": "user", "content": prompt})
    return messages


def _cost_sink(thread_id: str, turn_id: str):
    def record(**kwargs: Any) -> None:
        financial_llm_cost.record_call(
            thread_id=thread_id,
            turn_id=turn_id,
            node_name=_NODE,
            **kwargs,
        )

    return record


def _envelope(
    reply: str,
    *,
    thread_id: str,
    guarded: bool = False,
    capped: bool = False,
) -> dict[str, Any]:
    return {
        "reply": reply,
        "thread_id": thread_id,
        "guarded": guarded,
        "truncated": False,
        "capped": capped,
        "cost_usd": round(financial_llm_cost.thread_total_usd(thread_id), 6),
    }


async def _ask(messages: list[dict[str, str]], thread_id: str, turn_id: str) -> str | None:
    try:
        reply, provider = await chat_text(
            messages,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            tier="light",
            node_name=_NODE,
            cost_sink=_cost_sink(thread_id, turn_id),
        )
    except LlmError as exc:
        logger.warning("operation=agency_chat status=provider_failed thread=%s err=%s", thread_id, str(exc)[:200])
        return None
    if provider == "failed" or not reply:
        return None
    logger.info("operation=agency_chat status=answered thread=%s provider=%s", thread_id, provider)
    return reply.strip()


async def answer(
    *,
    thread_id: str,
    question: str,
    overview: dict[str, Any],
    prior_evidence: dict[str, Any] | None,
    history: list[dict[str, str]] | None = None,
    focus_id: str | None = None,
) -> dict[str, Any]:
    turn_id = uuid.uuid4().hex
    cleaned = (question or "").strip()
    if not cleaned:
        return _envelope("Ask me something about carryover, mapping, or the owner queue.", thread_id=thread_id)
    if len(cleaned) > MAX_QUESTION_CHARS:
        cleaned = cleaned[:MAX_QUESTION_CHARS]

    cap = settings.financial_chat_max_cost_usd
    if cap > 0 and financial_llm_cost.thread_total_usd(thread_id) >= cap:
        return _envelope(BUDGET_REPLY, thread_id=thread_id, capped=True)

    evidence = build_evidence(overview, prior_evidence=prior_evidence, for_snapshot=False)
    messages = build_chat_messages(evidence, history or [], cleaned, focus_id)
    reply = await _ask(messages, thread_id, turn_id)
    if not reply:
        return _envelope("I couldn't reach the model just now. Try again in a moment.", thread_id=thread_id)

    allowed = evidence_numbers(evidence)
    if offender := _unsupported_figure(reply, allowed):
        logger.info("operation=agency_chat status=guard_retry thread=%s figure=%s", thread_id, offender)
        retry = messages + [{
            "role": "user",
            "content": (
                "That answer stated a figure the evidence does not support. "
                "Answer again without unsupported quantities."
            ),
        }]
        reply = await _ask(retry, thread_id, turn_id)
        if not reply or _unsupported_figure(reply, allowed):
            return _envelope(GUARDED_REPLY, thread_id=thread_id, guarded=True)

    return _envelope(reply, thread_id=thread_id)
