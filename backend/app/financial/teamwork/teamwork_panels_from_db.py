"""Build Teamwork dashboard payloads from the Supabase mirror."""

from __future__ import annotations

import logging
from typing import Any

from app.financial.teamwork import teamwork_repository as repo

logger = logging.getLogger(__name__)


def list_projects(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_projects(site_id, **filters)


def list_tasks(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_tasks(site_id, **filters)


def list_people(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_people(site_id, **filters)


def list_milestones(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_milestones(site_id, **filters)


def list_timelogs(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_timelogs(site_id, **filters)


def _hours(minutes: int) -> float:
    return round((minutes or 0) / 60, 1)


def _project_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("project_id") or ""),
        "name": row.get("name") or "Untitled project",
        "status": row.get("status") or "",
        "health": row.get("health") or "unset",
        "company_name": row.get("company_name") or "",
        "start_date": row.get("start_date"),
        "due_date": row.get("due_date"),
        "tasks_open": int(row.get("tasks_open") or 0),
        "tasks_completed": int(row.get("tasks_completed") or 0),
        "tasks_overdue": int(row.get("tasks_overdue") or 0),
        "progress_pct": int(row.get("progress_pct") or 0),
    }


def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
    assignees = row.get("assignee_names")
    if not isinstance(assignees, list):
        assignees = []
    return {
        "id": str(row.get("task_id") or ""),
        "name": row.get("name") or "Untitled task",
        "status": row.get("status") or "",
        "priority": row.get("priority"),
        "due_date": row.get("due_date"),
        "project_id": str(row.get("project_id") or ""),
        "project_name": row.get("project_name") or "",
        "assignees": assignees,
    }


def _person_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("person_id") or ""),
        "name": row.get("name") or "",
        "email": row.get("email") or "",
        "title": row.get("title"),
        "company_name": row.get("company_name") or "",
    }


def _milestone_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("milestone_id") or ""),
        "name": row.get("name") or "Untitled milestone",
        "status": row.get("status") or "",
        "project_id": str(row.get("project_id") or ""),
        "project_name": row.get("project_name") or "",
        "due_date": row.get("due_date"),
        "progress_pct": row.get("progress_pct"),
    }


def _summarize_timelogs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_minutes = 0
    billable_minutes = 0
    by_person: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    for row in rows:
        minutes = int(row.get("minutes") or 0)
        total_minutes += minutes
        if row.get("billable"):
            billable_minutes += minutes
        user_id = str(row.get("user_id") or "")
        project_id = str(row.get("project_id") or "")
        if user_id:
            bucket = by_person.setdefault(
                user_id,
                {"id": user_id, "name": row.get("user_name") or user_id, "minutes": 0},
            )
            bucket["minutes"] += minutes
        if project_id:
            bucket = by_project.setdefault(
                project_id,
                {"id": project_id, "name": row.get("project_name") or project_id, "minutes": 0},
            )
            bucket["minutes"] += minutes
    return {
        "total_minutes": total_minutes,
        "billable_minutes": billable_minutes,
        "by_person": sorted(by_person.values(), key=lambda item: item["minutes"], reverse=True),
        "by_project": sorted(by_project.values(), key=lambda item: item["minutes"], reverse=True),
    }


def build_overview(site_id: str, *, as_of: str, computed_at: str | None = None) -> dict[str, Any]:
    projects = [_project_payload(row) for row in list_projects(site_id)]
    overdue_tasks = [_task_payload(row) for row in list_tasks(site_id, task_bucket="overdue")]
    upcoming_tasks = [_task_payload(row) for row in list_tasks(site_id, task_bucket="upcoming")]
    people = [_person_payload(row) for row in list_people(site_id)]
    milestones = [_milestone_payload(row) for row in list_milestones(site_id)]
    timelog_rows = list_timelogs(site_id)
    time_summary = _summarize_timelogs(timelog_rows)
    late_milestones = [row for row in milestones if str(row.get("status") or "").lower() == "late"]

    payload = {
        "connected": True,
        "generated_at": computed_at or as_of,
        "as_of": as_of,
        "cache_ttl_seconds": 0,
        "errors": {},
        "summary": {
            "project_count": len(projects),
            "overdue_task_count": len(overdue_tasks),
            "upcoming_task_count": len(upcoming_tasks),
            "late_milestone_count": len(late_milestones),
            "hours_this_month": _hours(time_summary["total_minutes"]),
            "people_count": len(people),
        },
        "projects": projects,
        "overdue_tasks": overdue_tasks,
        "upcoming_tasks": upcoming_tasks,
        "milestones": milestones,
        "people": people,
        "time": {
            "period_start": as_of[:8] + "01",
            "period_end": as_of,
            **time_summary,
        },
    }
    logger.info(
        "operation=teamwork_build_overview_from_db site_id=%s projects=%s overdue_tasks=%s",
        site_id,
        len(projects),
        len(overdue_tasks),
    )
    return payload
