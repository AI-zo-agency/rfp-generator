"""Pure capacity history and staffing signals derived from Teamwork snapshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

WEEKLY_CAPACITY_MINUTES = 2_400
CAPACITY_THRESHOLD_PCT = 85.0
CONSECUTIVE_WEEKS_REQUIRED = 3

_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_date(value: Any) -> date:
    return datetime.fromisoformat(str(value)[:10]).date()


def _week_start(row: dict[str, Any]) -> str:
    value = row.get("week_start")
    if value:
        return str(value)[:10]
    as_of = _as_date(row["as_of"])
    return (as_of - timedelta(days=as_of.weekday())).isoformat()


def _task_is_assigned_to(task: dict[str, Any], person_id: str, person_name: str) -> bool:
    for assignee in task.get("assignees") or []:
        if isinstance(assignee, dict):
            values = (assignee.get("id"), assignee.get("name"))
        else:
            values = (assignee,)
        if person_id in {str(value) for value in values if value is not None}:
            return True
        if person_name and person_name in {str(value) for value in values if value is not None}:
            return True
    return False


def build_daily_capacity_rows(overview: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    """Build one normalized capacity snapshot per person for a Teamwork day."""
    time_by_person = {
        str(row.get("id") or ""): row
        for row in (overview.get("time") or {}).get("by_person") or []
        if row.get("id") is not None
    }
    people = {
        str(row.get("id") or ""): row
        for row in overview.get("people") or []
        if row.get("id") is not None
    }
    people.update(
        {
            person_id: {"id": person_id, "name": row.get("name") or ""}
            for person_id, row in time_by_person.items()
            if person_id not in people
        }
    )
    projects = {str(row.get("id") or ""): row for row in overview.get("projects") or []}
    overdue = overview.get("overdue_tasks") or []
    due_soon = overview.get("upcoming_tasks") or []
    rows: list[dict[str, Any]] = []

    for person_id, person in people.items():
        person_name = str(person.get("name") or time_by_person.get(person_id, {}).get("name") or "")
        time_row = time_by_person.get(person_id, {})
        logged_minutes = _as_int(time_row.get("minutes"))
        active_project_ids = {
            str(part.get("id"))
            for part in time_row.get("breakdown") or []
            if part.get("id") is not None
        }
        for task in [*overdue, *due_soon]:
            if (
                _task_is_assigned_to(task, person_id, person_name)
                and task.get("project_id") is not None
            ):
                active_project_ids.add(str(task["project_id"]))
        active_projects = [projects[project_id] for project_id in active_project_ids if project_id in projects]
        budget_exposed_projects = sum(
            1
            for project in active_projects
            if _as_int(project.get("budget_capacity")) > 0
            and _as_int(project.get("budget_used")) * 100 >= _as_int(project.get("budget_capacity")) * 85
        )
        rows.append(
            {
                "person_id": person_id,
                "person_name": person_name,
                "logged_minutes": logged_minutes,
                "billable_minutes": _as_int(time_row.get("billable_minutes")),
                "capacity_minutes": WEEKLY_CAPACITY_MINUTES,
                "utilization_pct": round(logged_minutes / WEEKLY_CAPACITY_MINUTES * 100, 1),
                "overdue_tasks": sum(
                    _task_is_assigned_to(task, person_id, person_name) for task in overdue
                ),
                "due_soon_tasks": sum(
                    _task_is_assigned_to(task, person_id, person_name) for task in due_soon
                ),
                "active_projects": len(active_projects),
                "budget_exposed_projects": budget_exposed_projects,
            }
        )
    return sorted(rows, key=lambda row: (row["person_name"].casefold(), row["person_id"]))


def _latest_weekly_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in history:
        person_id = str(row.get("person_id") or "")
        if not person_id or (not row.get("as_of") and not row.get("week_start")):
            continue
        week_start = _week_start(row)
        key = (person_id, week_start)
        as_of = str(row.get("as_of") or row.get("week_start") or "")
        existing = latest.get(key)
        if existing is None or as_of > str(existing.get("as_of") or existing.get("week_start") or ""):
            latest[key] = {**row, "person_id": person_id, "week_start": week_start}
    return sorted(latest.values(), key=lambda row: (row["week_start"], row["person_id"]))


def is_sustained(rows: list[dict[str, Any]]) -> bool:
    ordered = sorted(rows, key=lambda row: row["week_start"], reverse=True)
    return len(ordered) >= CONSECUTIVE_WEEKS_REQUIRED and all(
        float(row.get("utilization_pct") or 0) >= CAPACITY_THRESHOLD_PCT
        for row in ordered[:CONSECUTIVE_WEEKS_REQUIRED]
    )


def capacity_history_state(history: list[dict[str, Any]]) -> dict[str, int | bool]:
    weeks = {row["week_start"] for row in _latest_weekly_rows(history)}
    return {"weeks_available": len(weeks), "ready": len(weeks) >= CONSECUTIVE_WEEKS_REQUIRED}


def capacity_signals(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic staffing cards once three weekly snapshots exist."""
    weekly_rows = _latest_weekly_rows(history)
    if not capacity_history_state(weekly_rows)["ready"]:
        return []

    by_person: dict[str, list[dict[str, Any]]] = {}
    for row in weekly_rows:
        by_person.setdefault(row["person_id"], []).append(row)

    signals: list[dict[str, Any]] = []
    sustained: list[dict[str, Any]] = []
    for person_id, rows in by_person.items():
        ordered = sorted(rows, key=lambda row: row["week_start"], reverse=True)
        current = ordered[0]
        person_name = str(current.get("person_name") or person_id)
        if is_sustained(rows):
            sustained.append(current)
            signals.append(
                {
                    "id": f"capacity:sustained:{person_id}",
                    "severity": "critical",
                    "headline": f"{person_name} is under sustained capacity pressure",
                    "figure": f"{float(current.get('utilization_pct') or 0):.1f}%",
                    "detail": (
                        "At or above 85% capacity for 3 consecutive weeks; "
                        f"latest week is {float(current.get('utilization_pct') or 0):.1f}%."
                    ),
                    "go_to": "team",
                }
            )
        elif float(current.get("utilization_pct") or 0) >= CAPACITY_THRESHOLD_PCT:
            signals.append(
                {
                    "id": f"capacity:watch:{person_id}",
                    "severity": "warn",
                    "headline": f"{person_name} is at capacity this week",
                    "figure": f"{float(current.get('utilization_pct') or 0):.1f}%",
                    "detail": "At or above the 85% capacity watch threshold; continue tracking weekly.",
                    "go_to": "team",
                }
            )

    if len(sustained) >= 2:
        names = ", ".join(
            sorted(str(row.get("person_name") or row["person_id"]) for row in sustained)
        )
        signals.extend(
            [
                {
                    "id": "capacity:team",
                    "severity": "critical",
                    "headline": "Team capacity pressure is sustained",
                    "figure": f"{len(sustained)} people",
                    "detail": (
                        f"{names} have each been at or above 85% capacity for 3 consecutive weeks."
                    ),
                    "go_to": "team",
                },
                {
                    "id": "capacity:hiring",
                    "severity": "info",
                    "headline": "Consider a staffing response",
                    "figure": f"{len(sustained)} people",
                    "detail": (
                        "Multiple people are under sustained capacity pressure; evaluate hiring, "
                        "contracting, or reprioritization."
                    ),
                    "go_to": "team",
                },
            ]
        )
    return sorted(signals, key=lambda signal: (_SEVERITY_RANK[signal["severity"]], signal["id"]))
