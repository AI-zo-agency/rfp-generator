"""Chat grounded in the same evidence the nightly brief was written from.

Deliberately not part of `qb_insights`: that module owns one scheduled call a
night that writes a brief, and mixing an interactive request path into it would
blur when a model call happens and who pays for it. What the two share is the
evidence builder and the figure guard, which is the point — chat must not be
able to claim more than the cards on screen can support.

Cost is recorded to `financial_llm_calls` through the `cost_sink` seam, never to
`llm_call_log`. See `financial_llm_cost` for why.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.core.config import settings
from app.financial import financial_llm_cost
from app.financial.figure_guard import (
    check_magnitude_claims,
    check_quantities,
    evidence_numbers,
)
from app.financial.qb_insights import _SYSTEM, build_evidence
from app.services.llm import LlmError, chat_text

logger = logging.getLogger(__name__)

# Answers run two to four sentences; 700 leaves room for a long one without
# paying for a model that decides to write an essay.
_MAX_TOKENS = 700
_TEMPERATURE = 0.2

# Turns kept from the thread. Long enough to follow a line of questioning,
# short enough that the evidence stays the bulk of the prompt.
MAX_HISTORY_TURNS = 8

# A question longer than this is not a question.
MAX_QUESTION_CHARS = 2000

_NODE = "qb_chat.answer"

GUARDED_REPLY = (
    "I can't answer that from the ledger data I have without stating a figure "
    "it doesn't back. Try asking about a specific client, cost bucket, or row."
)

BUDGET_REPLY = (
    "This conversation has reached its spending limit. Start a new one to keep "
    "going."
)

_CHAT_RULES = (
    "\n\nYou are answering the owner's question about this position, not writing "
    "the morning note. Two to four sentences, no headings and no lists.\n\n"
    "The evidence below is everything you know. When it does not contain the "
    "answer, say so in one sentence and name the report that would have it — "
    "do not reason toward a number you were not given. Every rule above about "
    "figures applies with equal force here: verbatim or not at all.\n\n"
    # An open question invites a comparison the brief never had to make, and the
    # guard only checks that a number appears in the evidence — not that it
    # appears attached to the right subject. A live answer put the overdue slice
    # of payables into the sentence "you owe X in bills", and read a revenue
    # growth percentage as a share of receivables. Both numbers were real; only
    # the sentences around them were wrong, and no numeric check can see that.
    "Two more rules, because a question invites comparisons the note never "
    "made:\n"
    "4. Never compute a share, percentage, ratio or proportion of one figure "
    "against another, even when both are in front of you. If a share is not "
    "written in the evidence, it is not available to you.\n"
    "5. Describe a figure using the evidence's own label for it. An overdue "
    "balance is what is overdue, not what is owed. If you cannot say what a "
    "number measures in the words it came with, leave it out."
)


def _unsupported_figure(text: str, allowed: set[float]) -> str | None:
    """The first quantity or magnitude claim in `text` the evidence cannot back.

    Same two checks the brief passes through, deliberately duplicated from
    `qb_insights` rather than imported: that one is a private helper of the
    nightly path, and a guard is the last thing that should acquire a shared
    owner who might loosen it for one caller.
    """
    return check_quantities(text, allowed) or check_magnitude_claims(text)


def _focus_row(evidence: dict[str, Any], focus_id: str | None) -> dict[str, Any] | None:
    """The row the reader pinned, if it is one we actually handed the model."""
    if not focus_id:
        return None
    for key in ("chase", "margin", "hygiene", "signals"):
        for row in evidence.get(key) or []:
            if row.get("id") == focus_id:
                return row
    return None


def build_chat_messages(
    evidence: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    focus_id: str | None = None,
) -> list[dict[str, str]]:
    """System prompt carries the evidence; history carries only the conversation.

    Evidence sits in the system message rather than being restated each turn so
    a long thread does not re-send the whole position on every question.
    """
    system = (
        f"{_SYSTEM}{_CHAT_RULES}\n\n"
        "Here is the current QuickBooks position:\n\n"
        f"{json.dumps(evidence, indent=2)}"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(_trim_history(history))

    focus = _focus_row(evidence, focus_id)
    if focus:
        question = (
            "The question is about this row:\n"
            f"{json.dumps(focus, indent=2)}\n\n"
            f"{question}"
        )
    messages.append({"role": "user", "content": question})
    return messages


def _trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Last MAX_HISTORY_TURNS exchanges, roles normalized, blanks dropped."""
    clean = [
        {"role": m["role"], "content": m["content"].strip()}
        for m in history
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    return clean[-(MAX_HISTORY_TURNS * 2) :]


def _cost_sink(thread_id: str, turn_id: str):
    """Binds this turn's identity to the financial ledger's record_call."""

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


def _envelope(
    reply: str,
    *,
    thread_id: str,
    guarded: bool = False,
    truncated: bool = False,
    capped: bool = False,
) -> dict[str, Any]:
    """One shape for every outcome, so streaming and tools can reuse it."""
    return {
        "reply": reply,
        "thread_id": thread_id,
        "guarded": guarded,
        "truncated": truncated,
        "capped": capped,
        "cost_usd": round(financial_llm_cost.thread_total_usd(thread_id), 6),
    }


async def answer(
    *,
    thread_id: str,
    question: str,
    overview: dict[str, Any],
    prior: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    focus_id: str | None = None,
) -> dict[str, Any]:
    """Answer one question against tonight's evidence.

    Never raises for a provider failure — an unreachable model is a message in
    the thread, not a 500 in a panel the reader was already looking at.
    """
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        return _envelope("Ask me something about the position.", thread_id=thread_id)

    cap = float(settings.financial_chat_max_cost_usd or 0)
    if cap > 0 and financial_llm_cost.thread_total_usd(thread_id) >= cap:
        logger.info("operation=qb_chat status=thread_capped thread=%s cap=%.2f", thread_id, cap)
        return _envelope(BUDGET_REPLY, thread_id=thread_id, capped=True)

    evidence = build_evidence(overview, prior)
    allowed = evidence_numbers(evidence)
    messages = build_chat_messages(evidence, history or [], question, focus_id)
    turn_id = uuid.uuid4().hex

    reply, offender = await _ask(messages, thread_id, turn_id)
    if reply is None:
        return _envelope(
            "I couldn't reach the model just now. Try again in a moment.",
            thread_id=thread_id,
        )

    if offender is None:
        offender = _unsupported_figure(reply, allowed)
    if offender:
        # One correction, quoting the offending claim back. A model that repeats
        # it is not going to be talked out of it on a third try.
        logger.info("operation=qb_chat status=guard_retry thread=%s figure=%s", thread_id, offender)
        retry = messages + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": (
                    f"That answer states {offender!r}, which is not in the data you "
                    "were given. Answer again using only figures copied verbatim "
                    "from the evidence, or say plainly that the data does not "
                    "contain it."
                ),
            },
        ]
        reply, _ = await _ask(retry, thread_id, turn_id)
        if reply is None or _unsupported_figure(reply, allowed):
            logger.info("operation=qb_chat status=guarded thread=%s", thread_id)
            return _envelope(GUARDED_REPLY, thread_id=thread_id, guarded=True)

    return _envelope(reply, thread_id=thread_id)


async def _ask(
    messages: list[dict[str, str]], thread_id: str, turn_id: str
) -> tuple[str | None, str | None]:
    """(reply, offender). A provider failure returns (None, None)."""
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
        logger.warning("operation=qb_chat status=provider_failed thread=%s err=%s",
                       thread_id, str(exc)[:200])
        return None, None
    text = (raw or "").strip()
    if not text:
        return None, None
    logger.info("operation=qb_chat status=answered thread=%s provider=%s", thread_id, provider)
    return text, None
