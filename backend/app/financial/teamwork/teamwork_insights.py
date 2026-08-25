"""Grounded, operational AI briefs for the Teamwork dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from app.financial.ai_insights_repository import upsert_insight
from app.financial.figure_guard import check_magnitude_claims, check_quantities, evidence_numbers
from app.financial.teamwork.teamwork_capacity import capacity_history_state, capacity_signals
from app.services.llm import chat_json_soft

logger = logging.getLogger(__name__)

SOURCE = "teamwork"
_MAX_TOKENS = 1400
_TEMPERATURE = 0.3
_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}
_PROHIBITED_CLAIMS = (
    re.compile(r"\b(?:cash|payroll)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:planned|unobserved)\s+(?:work|effort|hours?|capacity)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:work|effort|hours?|capacity)\s+(?:is\s+)?(?:planned|unobserved)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:effort|hours?)\s+(?:forecast|estimate)\b", re.IGNORECASE),
)
_HIRING_CLAIM = re.compile(r"\b(?:hire|hiring|recruit|recruiting)\b", re.IGNORECASE)

_SYSTEM = (
    "You write a concise delivery brief for a creative agency owner from Teamwork "
    "operational evidence. Explain delivery consequences and practical next actions. "
    "Teamwork is not a financial system: never claim cash, revenue, payroll, profit, "
    "or any conclusion about them. Never claim planned work, effort estimates, or "
    "unobserved capacity. Reuse supplied figures exactly or omit them; never calculate, "
    "round, approximate, or invent quantities. Only annotate the supplied signal ids."
)


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _signal(
    signal_id: str, severity: str, headline: str, figure: str, detail: str, go_to: str
) -> dict[str, str]:
    return {
        "id": signal_id,
        "severity": severity,
        "headline": headline,
        "figure": figure,
        "detail": detail,
        "go_to": go_to,
    }


def build_signals(overview: dict[str, Any], today: str) -> list[dict[str, str]]:
    """Derive deterministic delivery cards from the current Teamwork overview."""
    signals: list[dict[str, str]] = []
    overdue = overview.get("overdue_tasks") or []
    unassigned = [task for task in overdue if not (task.get("assignees") or [])]
    if unassigned:
        count = len(unassigned)
        signals.append(
            _signal(
                "overdue-unassigned",
                "critical",
                "Overdue work has no owner",
                f"{count} task{'s' if count != 1 else ''}",
                "Assign an owner before overdue delivery work can be recovered.",
                "tasks",
            )
        )

    current_day = _as_date(today)
    deadline_tasks = [
        task
        for task in overview.get("upcoming_tasks") or []
        if current_day is not None
        and (due_date := _as_date(task.get("due_date"))) is not None
        and 0 <= (due_date - current_day).days <= 7
    ]
    if deadline_tasks:
        count = len(deadline_tasks)
        signals.append(
            _signal(
                "deadline-pressure",
                "warn",
                "Delivery deadlines are close",
                f"{count} task{'s' if count != 1 else ''}",
                "Review owners and sequencing for work due in the next seven days.",
                "tasks",
            )
        )

    late_milestones = [
        milestone
        for milestone in overview.get("milestones") or []
        if str(milestone.get("status") or "").lower() == "late"
    ]
    if late_milestones:
        count = len(late_milestones)
        signals.append(
            _signal(
                "late-milestones",
                "critical",
                "Milestones are late",
                f"{count} milestone{'s' if count != 1 else ''}",
                "Confirm the recovery plan and communicate any delivery impact.",
                "projects",
            )
        )

    budget_exposed = [
        project
        for project in overview.get("projects") or []
        if int(project.get("budget_capacity") or 0) > 0
        and int(project.get("budget_used") or 0) * 100
        >= int(project.get("budget_capacity") or 0) * 85
    ]
    if budget_exposed:
        count = len(budget_exposed)
        signals.append(
            _signal(
                "budget-exposure",
                "warn",
                "Project budgets need delivery review",
                f"{count} project{'s' if count != 1 else ''}",
                "Check scope, remaining work, and delivery priorities in Teamwork.",
                "projects",
            )
        )

    return sorted(signals, key=lambda row: (_SEVERITY_RANK[row["severity"]], row["id"]))


def build_evidence(overview: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    today = str(overview.get("as_of") or "")
    signals = build_signals(overview, today) + capacity_signals(history)
    return {
        "signals": sorted(
            signals, key=lambda row: (_SEVERITY_RANK[row["severity"]], row["id"])
        ),
        "history": capacity_history_state(history),
        "freshness": {
            "sync_status": overview.get("sync_status"),
            "error_count": len(overview.get("errors") or {}),
        },
    }


def _known_ids(evidence: dict[str, Any]) -> set[str]:
    return {
        str(signal["id"])
        for signal in evidence.get("signals") or []
        if isinstance(signal, dict) and signal.get("id")
    }


def build_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    history_instruction = (
        "Staffing history is still building, so do not make a staffing recommendation."
        if not evidence.get("history", {}).get("ready")
        else "Only discuss staffing when the supplied capacity signal supports it."
    )
    user = (
        f"Here is today's Teamwork delivery evidence:\n\n{json.dumps(evidence, indent=2)}\n\n"
        "Write JSON exactly shaped as {\"brief\": \"...\", \"notes\": {\"<id>\": \"...\"}}. "
        "The brief is three or four sentences. Notes are one or two sentences and may use only "
        f"these signal ids: {sorted(_known_ids(evidence))}. {history_instruction}"
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _unsupported_figure(text: str, allowed: set[float]) -> str | None:
    return check_quantities(text, allowed) or check_magnitude_claims(text)


def _prohibited_claim(text: str, known_ids: set[str]) -> str | None:
    for pattern in _PROHIBITED_CLAIMS:
        if match := pattern.search(text):
            return match.group(0)
    if _HIRING_CLAIM.search(text) and "capacity:hiring" not in known_ids:
        return "hiring without a capacity:hiring signal"
    return None


def validate_response(raw: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    brief = raw.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("response has no usable brief")
    brief = brief.strip()
    allowed = evidence_numbers(evidence)
    known = _known_ids(evidence)
    if claim := _prohibited_claim(brief, known):
        raise ValueError(f"brief makes a prohibited claim: {claim!r}")
    if offender := _unsupported_figure(brief, allowed):
        raise ValueError(f"brief states an unsupported quantity: {offender!r}")

    notes: dict[str, str] = {}
    raw_notes = raw.get("notes")
    if isinstance(raw_notes, dict):
        for signal_id, note in raw_notes.items():
            if signal_id not in known or not isinstance(note, str) or not note.strip():
                continue
            note = note.strip()
            if _prohibited_claim(note, known):
                continue
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
        node_name="teamwork_insights",
    )
    if provider == "failed" or not raw:
        raise ValueError("provider returned nothing")
    return validate_response(raw, evidence), provider


def _store_quietly(site_id: str, as_of: str, **fields: Any) -> bool:
    try:
        upsert_insight(source=SOURCE, scope_key=site_id, as_of=as_of, model=None, **fields)
        return True
    except Exception:  # noqa: BLE001 -- insight storage cannot fail a sync
        logger.warning(
            "operation=teamwork_insights site_id=%s as_of=%s status=store_failed",
            site_id,
            as_of,
            exc_info=True,
        )
        return False


def generate_and_store(
    site_id: str, overview: dict[str, Any], history: list[dict[str, Any]], as_of: str
) -> str:
    """Best-effort generation for the nightly sync; always returns a status."""
    evidence: dict[str, Any] = {}
    try:
        evidence = build_evidence(overview, history)
        payload, provider = asyncio.run(_generate(evidence))
    except Exception as exc:  # noqa: BLE001 -- provider/model failures are non-fatal
        logger.warning(
            "operation=teamwork_insights site_id=%s as_of=%s status=failed reason=%s",
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
        payload=payload,
        evidence=evidence,
        provider=provider,
        status="ok",
        error=None,
    ):
        return "failed"
    logger.info(
        "operation=teamwork_insights site_id=%s as_of=%s status=ok provider=%s notes=%s",
        site_id,
        as_of,
        provider,
        len(payload["notes"]),
    )
    return "ok"
