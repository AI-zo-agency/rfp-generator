"""Nightly AI brief for the QuickBooks dashboard.

The model's entire output is prose: a short brief and one line per row. Every
number the reader sees was computed in Python and handed to the model as a
pre-formatted string, so a wrong figure in a leadership brief is not a risk that
has to be managed — there is no field the model could put one in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.financial.ai_insights_repository import upsert_insight
from app.financial.qb_insight_rows import chase_rows, hygiene_rows, row_ids
from app.financial.qb_signals import derive_signals
from app.services.llm import chat_json_soft

logger = logging.getLogger(__name__)

SOURCE = "quickbooks"
_MAX_TOKENS = 900
_TEMPERATURE = 0.3

_SYSTEM = (
    "You are the financial controller for a creative agency, writing a short "
    "morning note for the owner. Plain sentences, no jargon, no metric names, "
    "no bullet lists inside the brief. Never state a number, a percentage, or a "
    "client name that does not appear in the data you were given. If nothing is "
    "wrong, say so plainly rather than manufacturing concern."
)


def build_evidence(overview: dict[str, Any]) -> dict[str, Any]:
    return {
        "signals": derive_signals(overview),
        "chase": chase_rows(overview),
        "hygiene": hygiene_rows(overview),
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


def validate_response(
    raw: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """Keep the brief and the notes whose ids we actually sent. Discard the rest.

    Raises ValueError when there is no usable brief — that is a failed call.
    """
    brief = raw.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("response has no usable brief")

    known = row_ids(evidence["chase"]) | row_ids(evidence["hygiene"])
    raw_notes = raw.get("notes")
    notes: dict[str, str] = {}
    if isinstance(raw_notes, dict):
        for key, value in raw_notes.items():
            if key in known and isinstance(value, str) and value.strip():
                notes[key] = value.strip()
            else:
                logger.info(
                    "operation=qb_insights_validate status=note_dropped key=%s",
                    key,
                )
    return {"brief": brief.strip(), "notes": notes}


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
