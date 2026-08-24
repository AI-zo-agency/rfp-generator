"""Nightly AI brief for the QuickBooks dashboard.

The model's entire output is prose: a short brief and one line per row. Every
number the reader sees was computed in Python and handed to the model as a
pre-formatted string.

`notes` is structurally constrained — Python generates the row ids and validates
each note, so a note cannot carry a fabricated row or client.

`brief` is free prose and cannot be constrained that way. Three things defend
it, in descending order of how much work they do: the prohibitions in `_SYSTEM`,
the pre-computed quantities in `derived_figures` (so the model never has a
reason to derive one itself), and `figure_guard`, which rejects prose stating a
quantity the evidence cannot back — in verbal form as well as digit form,
because the first live brief got all three of its figures wrong without writing
a single digit. See `figure_guard` for what that check cannot catch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.financial.ai_insights_repository import upsert_insight
from app.financial.figure_guard import (
    check_magnitude_claims,
    check_quantities,
    evidence_numbers,
)
from app.financial.qb_insight_rows import chase_rows, hygiene_rows, row_ids
from app.financial.qb_position import position
from app.financial.qb_signals import derive_signals, derived_figures
from app.services.llm import chat_json_soft

logger = logging.getLogger(__name__)

SOURCE = "quickbooks"
_MAX_TOKENS = 900
_TEMPERATURE = 0.3

_SYSTEM = (
    "You are the financial controller for a creative agency, writing a short "
    "morning note for the owner. Plain sentences, no jargon, no metric names, "
    "no bullet lists inside the brief.\n\n"
    "Three rules about figures:\n"
    "1. Reuse figures verbatim or not at all. Copy the string you were given "
    'exactly — "$288,199", never "roughly $288k" and never "nearly '
    'three-quarters of a million". Do not round a figure, do not approximate '
    "one, and never write one out in words.\n"
    "2. Never derive a new quantity. No ratios, multiples, differences, sums, "
    "percentages or day-counts beyond the ones you were handed. Where a ratio "
    "is the natural way to put something it has already been computed for you; "
    "use that or say nothing.\n"
    '3. Never characterise magnitude across rows — "the bulk of", "most of", '
    '"the majority of" — unless the data states that share. Quote the stated '
    "share instead.\n\n"
    "Client names come from the data as well; never name one that is not in it. "
    "If nothing is wrong, say so plainly rather than manufacturing concern.\n\n"
    "The cash figures in `position` are printed on screen directly above your "
    "brief, where the reader can already see them. Lead with what they mean for "
    "the week ahead rather than reading them back."
)


# Keys the rows carry for ranking and for the frontend, which the model has no
# use for. Sending them is not neutral: every number in the evidence is a number
# the guard will accept, and a number the model may pair with the wrong subject.
#
# `dollar_days` is amount x days, a figure in the hundreds of thousands that
# resembles nothing the reader should see. OCF's 861,552 sat close enough to
# 750,000 to license "nearly three-quarters of a million" as a description of
# $288,199.
#
# `overdue_days` is the age of the single oldest invoice. Handed that beside an
# amount, the model writes "OCF is $11,966 overdue at 73 days" — the exact
# implicature the per-invoice split was built to remove, reintroduced in prose.
# `avg_overdue_days` is the one that pairs truthfully with the amount, so it is
# the only age the model gets. The UI still shows both.
_INTERNAL_ROW_KEYS = {"amount", "overdue_amount", "dollar_days", "overdue_days"}


def _model_facing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in row.items() if k not in _INTERNAL_ROW_KEYS} for row in rows
    ]


def build_evidence(overview: dict[str, Any]) -> dict[str, Any]:
    """Exactly what the model is shown, which is also what gets stored.

    Rows are projected down to the fields worth writing about. `figure` already
    carries every amount as a formatted string, so the raw numbers behind it add
    nothing to the prose and cost the guard its precision.
    """
    return {
        "position": position(overview),
        "signals": derive_signals(overview),
        "derived": derived_figures(overview),
        "chase": _model_facing(chase_rows(overview)),
        "hygiene": _model_facing(hygiene_rows(overview)),
    }


def build_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    ids = sorted(row_ids(evidence["chase"]) | row_ids(evidence["hygiene"]))
    user = (
        "Here is tonight's QuickBooks position.\n\n"
        f"{json.dumps(evidence, indent=2)}\n\n"
        "Write two things.\n\n"
        "1. `brief`: four or five sentences. Connect the signals to each other "
        "rather than restating them one by one — say what the combination means "
        "for the week ahead.\n"
        "2. `notes`: an object keyed by row id, each value one short sentence "
        "saying why that row matters now. Use only these ids, and omit any row "
        f"you have nothing useful to say about: {ids}\n\n"
        'Reply with JSON shaped exactly: {"brief": "...", "notes": {"<id>": "..."}}'
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def _unsupported_figure(text: str, allowed: set[float]) -> str | None:
    """The first quantity or magnitude claim in `text` the evidence cannot back."""
    return check_quantities(text, allowed) or check_magnitude_claims(text)


def validate_response(
    raw: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """Keep the brief and the notes whose ids and figures both check out.

    A note whose prose states a figure the evidence cannot back is dropped, the
    same way a note for an unknown row is — rows already render without notes,
    so the cost is one missing sentence.

    Raises ValueError when there is no usable brief, or when the brief itself
    states such a figure. Either is a failed call: `generate_and_store` records
    the reason and reads keep serving the last good brief.
    """
    brief = raw.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("response has no usable brief")
    brief = brief.strip()

    allowed = evidence_numbers(evidence)
    offender = _unsupported_figure(brief, allowed)
    if offender:
        raise ValueError(f"brief states an unsupported quantity: {offender!r}")

    known = row_ids(evidence["chase"]) | row_ids(evidence["hygiene"])
    raw_notes = raw.get("notes")
    notes: dict[str, str] = {}
    if isinstance(raw_notes, dict):
        for key, value in raw_notes.items():
            if key not in known or not isinstance(value, str) or not value.strip():
                logger.info(
                    "operation=qb_insights_validate status=note_dropped key=%s",
                    key,
                )
                continue
            text = value.strip()
            bad = _unsupported_figure(text, allowed)
            if bad:
                logger.info(
                    "operation=qb_insights_validate status=note_dropped_figure "
                    "key=%s figure=%s",
                    key,
                    bad,
                )
                continue
            notes[key] = text
    return {"brief": brief, "notes": notes}


async def _generate(evidence: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Returns (payload, provider). Raises ValueError when unusable."""
    raw, provider = await chat_json_soft(
        build_messages(evidence),
        max_tokens=_MAX_TOKENS,
        temperature=_TEMPERATURE,
        tier="light",
        node_name="qb_insights",
    )
    if provider == "failed" or not raw:
        raise ValueError("provider returned nothing")
    return validate_response(raw, evidence), provider


def generate_and_store(
    realm_id: str, overview: dict[str, Any], as_of: str
) -> str:
    """Generate tonight's brief and persist it. Returns "ok" or "failed".

    Never raises — the nightly sync must complete whether or not the model
    cooperates. Called from a sync FastAPI worker thread, so there is no running
    event loop and asyncio.run is safe.
    """
    evidence: dict[str, Any] = {}
    try:
        evidence = build_evidence(overview)
        payload, provider = asyncio.run(_generate(evidence))
    except Exception as exc:  # noqa: BLE001 — a bad brief must not fail the sync
        logger.warning(
            "operation=qb_insights realm_id=%s as_of=%s status=failed reason=%s",
            realm_id,
            as_of,
            str(exc)[:200],
        )
        _store_quietly(
            realm_id,
            as_of,
            payload={"brief": "", "notes": {}},
            evidence=evidence,
            provider=None,
            status="failed",
            error=str(exc),
        )
        return "failed"

    stored = _store_quietly(
        realm_id,
        as_of,
        payload=payload,
        evidence=evidence,
        provider=provider,
        status="ok",
        error=None,
    )
    if not stored:
        return "failed"
    logger.info(
        "operation=qb_insights realm_id=%s as_of=%s status=ok provider=%s notes=%s",
        realm_id,
        as_of,
        provider,
        len(payload["notes"]),
    )
    return "ok"


def _store_quietly(
    realm_id: str,
    as_of: str,
    *,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    provider: str | None,
    status: str,
    error: str | None,
) -> bool:
    try:
        upsert_insight(
            source=SOURCE,
            scope_key=realm_id,
            as_of=as_of,
            payload=payload,
            evidence=evidence,
            provider=provider,
            model=None,
            status=status,
            error=error,
        )
        return True
    except Exception:  # noqa: BLE001 — persistence failure must not fail the sync
        logger.warning(
            "operation=qb_insights realm_id=%s as_of=%s status=store_failed",
            realm_id,
            as_of,
            exc_info=True,
        )
        return False
