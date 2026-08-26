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
from app.services.llm import chat_json_soft, resolve_llm_model

logger = logging.getLogger(__name__)

SOURCE = "teamwork"
# 3.6 Flash spends ~3k tokens thinking before it writes. 8192 leaves room for
# the JSON brief after that; 3000 truncated live (finish_reason=length).
_MAX_TOKENS = 8192
_TEMPERATURE = 0.3
_NAMED_CAP = 8
_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}
_PROHIBITED_CLAIMS = (
    re.compile(r"\b(?:cash|payroll|salar(?:y|ies)|wages?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:planned|unobserved|estimated|forecast(?:ed|ing)?)\b"
        r"(?:\W+\w+){0,4}\W+\b(?:work|effort|hours?|capacity)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:work|effort|hours?|capacity)\b(?:\W+\w+){0,4}\W+"
        r"\b(?:planned|unobserved|estimated|forecast(?:ed|ing)?)\b",
        re.IGNORECASE,
    ),
)
_HIRING_CLAIM = re.compile(r"\b(?:hire|hiring|recruit|recruiting)\b", re.IGNORECASE)

_SYSTEM = (
    "You write a delivery brief for a creative agency owner from Teamwork "
    "operational evidence. Every sentence names a project, task, person, or "
    "milestone from the evidence and says what to do with it. Generic advice "
    "such as confirm a plan or review ownership is not useful unless it names "
    "where. Teamwork is not a financial system: never claim cash, revenue, "
    "payroll, profit, or any conclusion about them. Never claim planned work, "
    "effort estimates, or unobserved capacity. Reuse supplied figures exactly "
    "or omit them; never calculate, round, approximate, or invent quantities. "
    "Only annotate the supplied signal ids. Names come from the evidence; never "
    "invent a person, project, or task that is not in it."
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


def _assignee_names(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for assignee in task.get("assignees") or []:
        if isinstance(assignee, dict):
            name = str(assignee.get("name") or "").strip()
        else:
            name = str(assignee or "").strip()
        if name:
            names.append(name)
    return names


def _name_list(names: list[str], max_n: int = 3) -> str:
    shown = [name for name in names if name][:max_n]
    rest = max(0, len([name for name in names if name]) - len(shown))
    if rest:
        return f"{', '.join(shown)} +{rest} more"
    return ", ".join(shown)


def _days_late(due: Any, today: str) -> int | None:
    due_day = _as_date(due)
    today_day = _as_date(today)
    if due_day is None or today_day is None:
        return None
    delta = (today_day - due_day).days
    return delta if delta > 0 else None


def _task_ref(task: dict[str, Any], today: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task": str(task.get("name") or ""),
        "project": str(task.get("project_name") or ""),
        "owners": _assignee_names(task),
    }
    days = _days_late(task.get("due_date"), today)
    if days is not None:
        row["days_late"] = days
    due = task.get("due_date")
    if due:
        row["due"] = str(due)[:10]
    return row


def _overdue_by_project(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for task in tasks:
        project = str(task.get("project_name") or "No project")
        key = str(task.get("project_id") or project)
        bucket = buckets.setdefault(key, {"project": project, "count": 0})
        bucket["count"] += 1
    return sorted(buckets.values(), key=lambda row: (-int(row["count"]), str(row["project"]).casefold()))


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def build_signals(overview: dict[str, Any], today: str) -> list[dict[str, str]]:
    """Derive deterministic delivery cards from the current Teamwork overview."""
    signals: list[dict[str, str]] = []
    overdue = overview.get("overdue_tasks") or []
    unassigned = [task for task in overdue if not _assignee_names(task)]
    if unassigned:
        count = len(unassigned)
        labels = [
            f"{task.get('name') or 'Untitled task'} ({task.get('project_name') or 'No project'})"
            for task in unassigned
        ]
        signals.append(
            _signal(
                "overdue-unassigned",
                "critical",
                "Overdue work has no owner",
                f"{count} {_plural(count, 'task')}",
                f"{_name_list(labels)}. Assign an owner on each task today.",
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
        ranked = _overdue_by_project(deadline_tasks)
        labels = [f"{row['project']} ({row['count']})" for row in ranked]
        signals.append(
            _signal(
                "deadline-pressure",
                "warn",
                "Delivery deadlines are close",
                f"{count} {_plural(count, 'task')}",
                f"{_name_list(labels)}. Confirm each owner can finish this week.",
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
        labels = [
            f"{milestone.get('name') or 'Untitled milestone'} "
            f"({milestone.get('project_name') or 'No project'})"
            for milestone in late_milestones
        ]
        signals.append(
            _signal(
                "late-milestones",
                "critical",
                "Milestones are late",
                f"{count} {_plural(count, 'milestone')}",
                f"{_name_list(labels)}. Reset the date with each project's owner.",
                "projects",
            )
        )

    ranked_overdue = _overdue_by_project(overdue)
    if ranked_overdue:
        top = ranked_overdue[0]
        top_count = int(top["count"])
        rest_labels = [f"{row['project']} ({row['count']})" for row in ranked_overdue[1:]]
        if len(ranked_overdue) == 1 or top_count >= 10:
            signals.append(
                _signal(
                    "overdue-concentration",
                    "critical" if top_count >= 10 else "warn",
                    f"{top['project']} holds {top_count} overdue {_plural(top_count, 'task')}",
                    f"{top_count} {_plural(top_count, 'task')}",
                    (
                        f"{_name_list(rest_labels)}. Recover this project first."
                        if rest_labels
                        else "Concentrate recovery on this project today."
                    ),
                    "tasks",
                )
            )
        else:
            lead = min(3, len(ranked_overdue))
            lead_labels = [f"{row['project']} ({row['count']})" for row in ranked_overdue[:lead]]
            signals.append(
                _signal(
                    "overdue-concentration",
                    "warn",
                    f"{lead} {_plural(lead, 'project')} hold most overdue",
                    f"{lead} {_plural(lead, 'project')}",
                    f"{_name_list(lead_labels)}. Start with the hottest project.",
                    "tasks",
                )
            )

    oldest: dict[str, Any] | None = None
    oldest_days = 0
    for task in overdue:
        days = _days_late(task.get("due_date"), today)
        if days is not None and days > oldest_days:
            oldest = task
            oldest_days = days
    if oldest and oldest_days:
        signals.append(
            _signal(
                "oldest-overdue",
                "critical" if oldest_days >= 14 else "warn",
                f"Oldest overdue task is {oldest_days} days late",
                f"{oldest_days}d",
                (
                    f"{oldest.get('name') or 'Untitled task'} · "
                    f"{oldest.get('project_name') or 'No project'}. "
                    "Get a completion date from the owner today."
                ),
                "tasks",
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
                f"{count} {_plural(count, 'project')}",
                (
                    f"{_name_list([str(project.get('name') or '') for project in budget_exposed])}. "
                    "Check remaining scope in Teamwork before more time is logged."
                ),
                "projects",
            )
        )

    return sorted(signals, key=lambda row: (_SEVERITY_RANK[row["severity"]], row["id"]))


def _named_evidence(overview: dict[str, Any], today: str) -> dict[str, Any]:
    """Hottest named rows the model may quote. Capped samples, not exhaustive lists."""
    overdue = overview.get("overdue_tasks") or []
    unassigned = [task for task in overdue if not _assignee_names(task)]
    current_day = _as_date(today)
    deadline_tasks = [
        task
        for task in overview.get("upcoming_tasks") or []
        if current_day is not None
        and (due_date := _as_date(task.get("due_date"))) is not None
        and 0 <= (due_date - current_day).days <= 7
    ]
    late_milestones = [
        milestone
        for milestone in overview.get("milestones") or []
        if str(milestone.get("status") or "").lower() == "late"
    ]
    budget_exposed = [
        project
        for project in overview.get("projects") or []
        if int(project.get("budget_capacity") or 0) > 0
        and int(project.get("budget_used") or 0) * 100
        >= int(project.get("budget_capacity") or 0) * 85
    ]
    oldest: dict[str, Any] | None = None
    oldest_days = 0
    for task in overdue:
        days = _days_late(task.get("due_date"), today)
        if days is not None and days > oldest_days:
            oldest = task
            oldest_days = days
    named: dict[str, Any] = {}
    if unassigned:
        named["unassigned_overdue"] = [_task_ref(task, today) for task in unassigned[:_NAMED_CAP]]
    if ranked := _overdue_by_project(overdue):
        named["overdue_by_project"] = ranked[:_NAMED_CAP]
    if oldest:
        named["oldest_overdue"] = _task_ref(oldest, today)
    if late_milestones:
        named["late_milestones"] = [
            {
                "milestone": str(row.get("name") or ""),
                "project": str(row.get("project_name") or ""),
                "due": str(row.get("due_date") or "")[:10],
            }
            for row in late_milestones[:_NAMED_CAP]
        ]
    if deadline_tasks:
        named["due_this_week_by_project"] = _overdue_by_project(deadline_tasks)[:_NAMED_CAP]
        named["due_this_week"] = [_task_ref(task, today) for task in deadline_tasks[:_NAMED_CAP]]
    if budget_exposed:
        named["budget_exposed"] = [
            {"project": str(row.get("name") or "")} for row in budget_exposed[:_NAMED_CAP]
        ]
    return named


def build_evidence(overview: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    today = str(overview.get("as_of") or "")
    signals = build_signals(overview, today) + capacity_signals(history)
    evidence: dict[str, Any] = {
        "signals": sorted(
            signals, key=lambda row: (_SEVERITY_RANK[row["severity"]], row["id"])
        ),
        "history": capacity_history_state(history),
        "freshness": {
            "sync_status": overview.get("sync_status"),
            "error_count": len(overview.get("errors") or {}),
        },
    }
    named = _named_evidence(overview, today)
    if named:
        evidence["named"] = named
    return evidence


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
        "Write JSON exactly shaped as {\"notes\": {\"<id>\": \"...\"}, \"brief\": \"...\"}. "
        "Write notes first. Each note is two or three sentences: where (project and task "
        "or milestone names copied from the evidence), who if an owner is listed, and what "
        "to do next. The brief is three or four sentences naming the hottest projects and "
        "the first actions. Lists under named are samples of the hottest items, not "
        "exhaustive — do not imply they are the complete set. "
        f"Notes may use only these signal ids: {sorted(_known_ids(evidence))}. "
        f"{history_instruction}"
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
        upsert_insight(
            source=SOURCE,
            scope_key=site_id,
            as_of=as_of,
            model=resolve_llm_model("light", node_name="teamwork_insights"),
            **fields,
        )
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
