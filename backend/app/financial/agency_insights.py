"""Weekly grounded AI briefs for the Agency owner control room."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from app.financial.agency_carryover import apply_carryover_state
from app.financial.agency_signals import build_signals
from app.financial.agency_trackables import build_trackable_items, extract_kpis
from app.financial.agency_week import (
    brief_week_for,
    current_week_label,
    iso,
    prior_week_bounds,
    today_pt,
    week_bounds,
)
from app.financial.ai_insights_repository import upsert_insight
from app.financial.figure_guard import check_magnitude_claims, check_quantities, evidence_numbers
from app.services.llm import chat_json_soft, resolve_llm_model

logger = logging.getLogger(__name__)

SOURCE = "agency"
_MAX_TOKENS = 8192
_TEMPERATURE = 0.3

_SYSTEM = (
    "You write a weekly Agency brief for an owner who joins Teamwork delivery, "
    "QuickBooks money, and client-map relationships. Plain US English sentences, "
    "no bullet lists inside the brief.\n\n"
    "Agency is the join layer — do not restate full QuickBooks AR chase lists or "
    "Teamwork task inventories. Focus on carryover, mapping gaps, reconciliation, "
    "and what still needs an owner.\n\n"
    "Never invent team members, clients, payment collected status, or deliveries the "
    "evidence does not show. Reuse supplied figures verbatim or omit them. Never "
    "derive new quantities, ratios, or percentages."
)


def _known_ids(evidence: dict[str, Any]) -> set[str]:
    ids = {str(row.get("id")) for row in evidence.get("signals") or [] if row.get("id")}
    return ids


def preserve_snapshot_items(
    current_items: list[dict[str, Any]],
    prior_open: list[dict[str, Any]] | None,
    *,
    week_start: str,
) -> list[dict[str, Any]]:
    """Friday snapshot — keep aging metadata without incrementing weeks_open."""
    prior_by_id = {str(row["id"]): row for row in (prior_open or []) if row.get("id")}
    preserved: list[dict[str, Any]] = []
    for item in current_items:
        row = {**item}
        prior = prior_by_id.get(str(item["id"]))
        if prior:
            row["first_seen_week"] = str(prior.get("first_seen_week") or week_start)
            row["weeks_open"] = int(prior.get("weeks_open") or 1)
        else:
            row["first_seen_week"] = week_start
            row["weeks_open"] = 1
        row["carryover"] = False
        preserved.append(row)
    return preserved


def build_evidence(
    overview: dict[str, Any],
    *,
    prior_evidence: dict[str, Any] | None = None,
    for_snapshot: bool = False,
    reference_day: date | None = None,
) -> dict[str, Any]:
    day = reference_day or today_pt()
    brief_start, brief_end, label = brief_week_for(day)
    current_start, current_end = week_bounds(day)
    raw_items = build_trackable_items(overview)
    prior_open = (prior_evidence or {}).get("open_items") if prior_evidence else None
    prior_kpis = (prior_evidence or {}).get("kpis") if prior_evidence else None
    has_prior_snapshot = isinstance(prior_open, list) and len(prior_open) > 0

    if for_snapshot:
        week_start = iso(current_start)
        open_items = preserve_snapshot_items(raw_items, prior_open, week_start=week_start)
        carryover, resolved, new_items = [], [], []
    else:
        week_start = iso(current_start)
        open_items, carryover, resolved, new_items = apply_carryover_state(
            raw_items,
            prior_open if isinstance(prior_open, list) else None,
            week_start=week_start,
        )

    kpis = extract_kpis(overview, open_items)
    signals = build_signals(
        overview=overview,
        open_items=open_items,
        carryover=carryover,
        resolved=resolved,
        new_items=new_items,
        kpis=kpis,
        prior_kpis=prior_kpis if isinstance(prior_kpis, dict) else None,
        brief_week_start=iso(brief_start),
        brief_week_end=iso(brief_end),
        has_prior_snapshot=has_prior_snapshot,
    )
    return {
        "cadence": "weekly",
        "brief_week_start": iso(brief_start),
        "brief_week_end": iso(brief_end),
        "period_label": label,
        "current_week_start": iso(current_start),
        "current_week_end": iso(current_end),
        "current_week_label": current_week_label(day),
        "has_prior_snapshot": has_prior_snapshot,
        "kpis": kpis,
        "open_items": open_items,
        "carryover": carryover,
        "resolved": resolved,
        "new": new_items,
        "signals": signals,
    }


def build_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    ids = sorted(_known_ids(evidence))
    period = evidence.get("period_label") or "the prior week"
    bootstrap = not evidence.get("has_prior_snapshot")
    bootstrap_note = (
        "There is no prior Friday snapshot yet, so nothing truly 'carried over' or "
        "'cleared last week.' Describe the current owner queue baseline — overdue "
        "delivery, mapping gaps, unlinked invoices, and orphans — without calling "
        "open items 'new this week.'"
        if bootstrap
        else (
            "Focus on what carried over from the prior week, what cleared, what is "
            "aging, and what the owner should focus on this week."
        )
    )
    user = (
        f"Here is Agency evidence for weekly insights ({period}).\n\n"
        f"{json.dumps(evidence, indent=2)}\n\n"
        "Write two things.\n\n"
        f"1. `brief`: four or five sentences. {bootstrap_note} "
        "Name specific clients or projects from the evidence.\n"
        "2. `notes`: an object keyed by signal id with one or two sentences each — "
        "consequence and action, not a restatement of the headline. Use only these ids "
        f"and omit rows you cannot add to: {ids}\n\n"
        'Reply with JSON shaped exactly: {"brief": "...", "notes": {"<id>": "..."}}'
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def _unsupported_figure(text: str, allowed: set[float]) -> str | None:
    return check_quantities(text, allowed) or check_magnitude_claims(text)


def validate_response(raw: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    brief = raw.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("response has no usable brief")
    brief = brief.strip()
    allowed = evidence_numbers(evidence)
    if offender := _unsupported_figure(brief, allowed):
        raise ValueError(f"brief states an unsupported quantity: {offender!r}")

    known = _known_ids(evidence)
    notes: dict[str, str] = {}
    raw_notes = raw.get("notes")
    if isinstance(raw_notes, dict):
        for signal_id, note in raw_notes.items():
            if signal_id not in known or not isinstance(note, str) or not note.strip():
                continue
            note = note.strip()
            if _unsupported_figure(note, allowed):
                continue
            notes[signal_id] = note
    return {"brief": brief, "notes": notes}


async def _generate(evidence: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw, provider = await chat_json_soft(
        build_messages(evidence),
        max_tokens=_MAX_TOKENS,
        temperature=_TEMPERATURE,
        tier="light",
        node_name="agency_insights",
    )
    if provider == "failed" or not raw:
        raise ValueError("provider returned nothing")
    return validate_response(raw, evidence), provider


def _store_quietly(site_id: str, as_of: str, **fields: Any) -> bool:
    try:
        upsert_insight(
            source=SOURCE,
            scope_key=site_id,
            as_of=as_of,
            model=resolve_llm_model("light", node_name="agency_insights"),
            **fields,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            "operation=agency_insights site_id=%s as_of=%s status=store_failed",
            site_id,
            as_of,
            exc_info=True,
        )
        return False


def store_snapshot(site_id: str, overview: dict[str, Any], prior_evidence: dict[str, Any] | None) -> str:
    """Friday job — persist open items and KPIs without LLM."""
    day = today_pt()
    monday, _ = week_bounds(day)
    as_of = iso(monday)
    evidence = build_evidence(overview, prior_evidence=prior_evidence, for_snapshot=True, reference_day=day)
    ok = _store_quietly(
        site_id,
        as_of,
        payload={
            "brief": "",
            "notes": {},
            "period_label": evidence.get("period_label"),
            "current_week_label": evidence.get("current_week_label"),
            "cadence": "weekly",
        },
        evidence=evidence,
        provider=None,
        status="ok",
        error=None,
    )
    logger.info(
        "operation=agency_insights_snapshot site_id=%s as_of=%s open_items=%s status=%s",
        site_id,
        as_of,
        len(evidence.get("open_items") or []),
        "ok" if ok else "failed",
    )
    return "ok" if ok else "failed"


def generate_and_store(
    site_id: str,
    overview: dict[str, Any],
    prior_evidence: dict[str, Any] | None,
) -> str:
    """Monday job — carryover diff, LLM brief, store under prior week Monday."""
    day = today_pt()
    brief_start, _, _ = brief_week_for(day)
    as_of = iso(brief_start)
    evidence: dict[str, Any] = {}
    try:
        evidence = build_evidence(overview, prior_evidence=prior_evidence, for_snapshot=False, reference_day=day)
        payload, provider = asyncio.run(_generate(evidence))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "operation=agency_insights site_id=%s as_of=%s status=failed reason=%s",
            site_id,
            as_of,
            str(exc)[:200],
        )
        _store_quietly(
            site_id,
            as_of,
            payload={"brief": "", "notes": {}},
            evidence=evidence,
            provider=None,
            status="failed",
            error=str(exc),
        )
        return "failed"

    if not _store_quietly(
        site_id,
        as_of,
        payload={
            "brief": payload["brief"],
            "notes": payload["notes"],
            "period_label": evidence.get("period_label"),
            "current_week_label": evidence.get("current_week_label"),
            "cadence": "weekly",
        },
        evidence=evidence,
        provider=provider,
        status="ok",
        error=None,
    ):
        return "failed"
    logger.info(
        "operation=agency_insights site_id=%s as_of=%s status=ok notes=%s",
        site_id,
        as_of,
        len(payload["notes"]),
    )
    return "ok"
