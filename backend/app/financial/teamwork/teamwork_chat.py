"""Grounded interactive answers for the Teamwork delivery dashboard."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from app.core.config import settings
from app.financial import financial_llm_cost
from app.financial.figure_guard import check_magnitude_claims, check_quantities, evidence_numbers
from app.financial.teamwork.teamwork_insights import _SYSTEM, _prohibited_claim, build_evidence
from app.services.llm import LlmError, chat_text

logger = logging.getLogger(__name__)

_MAX_TOKENS = 700
_TEMPERATURE = 0.2
MAX_HISTORY_TURNS = 8
MAX_QUESTION_CHARS = 2000
_NODE = "teamwork_chat.answer"

GUARDED_REPLY = (
    "I can't answer that from the Teamwork delivery data I have without stating "
    "a figure or conclusion it doesn't back. Try asking about a current signal or task."
)
BUDGET_REPLY = "This conversation has reached its spending limit. Start a new one to keep going."
FORECAST_UNAVAILABLE_REPLY = (
    "That data is unavailable: Teamwork does not include forecast inputs such as "
    "planned work or effort estimates."
)

_FORECAST_QUESTION = re.compile(
    r"\b(?:forecast|projection|predict|next\s+(?:week|month|quarter)|"
    r"future\s+(?:workload|capacity|hours?|work)|will\s+(?:we|the\s+team))\b",
    re.IGNORECASE,
)
_CHAT_RULES = (
    "\n\nYou answer an owner's question about current delivery work, not a daily brief. "
    "Use two to four sentences with no heading or list. The supplied evidence is "
    "everything you know. Reuse supplied figures exactly or omit them; never calculate, "
    "round, approximate, or invent quantities. Do not claim cash, revenue, payroll, "
    "profit, planned work, effort estimates, or unobserved capacity. If information is "
    "not in the evidence, say it is unavailable rather than infer it."
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
    evidence: dict[str, Any], history: list[dict[str, str]], question: str, focus_id: str | None = None
) -> list[dict[str, str]]:
    messages = [{
        "role": "system",
        "content": f"{_SYSTEM}{_CHAT_RULES}\n\nCurrent Teamwork delivery evidence:\n\n{json.dumps(evidence, indent=2)}",
    }]
    messages.extend(_trim_history(history))
    if focus := _focus_signal(evidence, focus_id):
        question = f"The question is about this signal:\n{json.dumps(focus, indent=2)}\n\n{question}"
    messages.append({"role": "user", "content": question})
    return messages


def _cost_sink(thread_id: str, turn_id: str):
    def sink(*, model: str, tier: str, provider: str, usage: dict[str, Any], latency_ms: int) -> None:
        financial_llm_cost.record_call(
            thread_id=thread_id,
            turn_id=turn_id,
            node_name=_NODE,
            model=model,
            tier=tier,
            provider=provider,
            usage=usage,
            latency_ms=latency_ms,
        )

    return sink


def _envelope(reply: str, *, thread_id: str, guarded: bool = False, capped: bool = False) -> dict[str, Any]:
    return {
        "reply": reply,
        "thread_id": thread_id,
        "guarded": guarded,
        "truncated": False,
        "capped": capped,
        "cost_usd": round(financial_llm_cost.thread_total_usd(thread_id), 6),
    }


def _needs_forecast_inputs(question: str) -> bool:
    return bool(_FORECAST_QUESTION.search(question))


async def _ask(messages: list[dict[str, str]], thread_id: str, turn_id: str) -> str | None:
    try:
        raw, provider = await chat_text(
            messages,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            tier="light",
            node_name=_NODE,
            cost_sink=_cost_sink(thread_id, turn_id),
        )
    except LlmError as exc:
        logger.warning("operation=teamwork_chat status=provider_failed thread=%s err=%s", thread_id, str(exc)[:200])
        return None
    reply = (raw or "").strip()
    if reply:
        logger.info("operation=teamwork_chat status=answered thread=%s provider=%s", thread_id, provider)
    return reply or None


async def answer(
    *,
    thread_id: str,
    question: str,
    overview: dict[str, Any],
    capacity_history: list[dict[str, Any]] | None = None,
    history: list[dict[str, str]] | None = None,
    focus_id: str | None = None,
) -> dict[str, Any]:
    """Answer one question without extending beyond current Teamwork evidence."""
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        return _envelope("Ask me something about current delivery work.", thread_id=thread_id)
    if _needs_forecast_inputs(question):
        return _envelope(FORECAST_UNAVAILABLE_REPLY, thread_id=thread_id)

    cap = float(settings.financial_chat_max_cost_usd or 0)
    if cap > 0 and financial_llm_cost.thread_total_usd(thread_id) >= cap:
        return _envelope(BUDGET_REPLY, thread_id=thread_id, capped=True)

    # Capacity history belongs to evidence, while chat history is only conversation context.
    evidence = build_evidence(overview, capacity_history or [])
    allowed = evidence_numbers(evidence)
    messages = build_chat_messages(evidence, history or [], question, focus_id)
    turn_id = uuid.uuid4().hex
    reply = await _ask(messages, thread_id, turn_id)
    if reply is None:
        return _envelope("I couldn't reach the model just now. Try again in a moment.", thread_id=thread_id)

    offender = _unsupported_figure(reply, allowed) or _prohibited_claim(
        reply, {str(row.get("id")) for row in evidence.get("signals") or []}
    )
    if offender:
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": f"That answer states {offender!r}, which is not backed by the evidence. Answer again using only the evidence, or say the data is unavailable."},
        ]
        reply = await _ask(retry, thread_id, turn_id)
        if reply is None or _unsupported_figure(reply, allowed) or _prohibited_claim(
            reply, {str(row.get("id")) for row in evidence.get("signals") or []}
        ):
            return _envelope(GUARDED_REPLY, thread_id=thread_id, guarded=True)
    return _envelope(reply, thread_id=thread_id)
