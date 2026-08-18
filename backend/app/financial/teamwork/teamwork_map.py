"""Normalize Teamwork API payloads into Supabase mirror rows."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def site_id_from_base_url(base_url: str) -> str:
    raw = (base_url or "").strip().rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return parsed.netloc or raw


def _ts(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)[:10]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


def _ref_id(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _name(row: dict[str, Any]) -> str:
    first = str(row.get("firstName") or "").strip()
    last = str(row.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return full or str(row.get("email") or row.get("name") or "")


def _task_counts(project: dict[str, Any]) -> tuple[int, int, int]:
    stats = project.get("stats") if isinstance(project.get("stats"), dict) else {}
    tasks = stats.get("tasks") if isinstance(stats.get("tasks"), dict) else {}
    if not tasks:
        tasks = project.get("taskCounts") if isinstance(project.get("taskCounts"), dict) else {}
    open_count = _int(tasks.get("active") or tasks.get("open") or tasks.get("incomplete"))
    completed = _int(tasks.get("completed") or tasks.get("complete"))
    overdue = _int(tasks.get("late") or tasks.get("overdue"))
    return open_count, completed, overdue


def _health(project: dict[str, Any]) -> str:
    health = _int(project.get("health"), -1)
    return {1: "bad", 2: "ok", 3: "good"}.get(health, "unset")


def map_project(*, site_id: str, project: dict[str, Any], included: dict[str, Any], synced_at: str) -> dict[str, Any]:
    company_id = _ref_id(project.get("companyId") or project.get("company"))
    companies = included.get("companies") if isinstance(included.get("companies"), dict) else {}
    company = companies.get(str(company_id)) if company_id is not None else None
    open_count, completed, overdue = _task_counts(project)
    total = open_count + completed
    row = {
        "site_id": site_id,
        "project_id": _ref_id(project.get("id")) or 0,
        "name": str(project.get("name") or "Untitled project"),
        "status": str(project.get("status") or ""),
        "health": _health(project),
        "company_id": company_id,
        "company_name": str((company or {}).get("name") or project.get("companyName") or ""),
        "start_date": _date(project.get("startDate")),
        "due_date": _date(project.get("endDate") or project.get("dueDate")),
        "tasks_open": open_count,
        "tasks_completed": completed,
        "tasks_overdue": overdue,
        "progress_pct": round((completed / total) * 100) if total else 0,
        "updated_at_remote": _ts(project.get("updatedAt") or project.get("lastChangedOn")),
        "synced_at": synced_at,
        "raw": project,
    }
    logger.debug("operation=teamwork_map_project site_id=%s project_id=%s", site_id, row["project_id"])
    return row


def _assignee_names(task: dict[str, Any], included: dict[str, Any]) -> list[str]:
    users = included.get("users") if isinstance(included.get("users"), dict) else {}
    people = included.get("people") if isinstance(included.get("people"), dict) else {}
    assignee_ids = task.get("assigneeUserIds")
    if not isinstance(assignee_ids, list):
        assignee = task.get("assignee")
        if isinstance(assignee, dict):
            assignee_ids = [assignee.get("id")]
        else:
            assignee_ids = []
    names = []
    for raw_id in assignee_ids:
        key = str(raw_id)
        person = users.get(key) or people.get(key) or {}
        resolved = _name(person) or key
        names.append(resolved)
    return names


def map_task(
    *,
    site_id: str,
    task: dict[str, Any],
    included: dict[str, Any],
    task_bucket: str,
    synced_at: str,
) -> dict[str, Any]:
    project_id = _ref_id(task.get("projectId") or task.get("project"))
    projects = included.get("projects") if isinstance(included.get("projects"), dict) else {}
    project = projects.get(str(project_id)) if project_id is not None else None
    row = {
        "site_id": site_id,
        "task_id": _ref_id(task.get("id")) or 0,
        "name": str(task.get("name") or "Untitled task"),
        "status": str(task.get("status") or ""),
        "priority": str(task.get("priority") or "") or None,
        "project_id": project_id,
        "project_name": str((project or {}).get("name") or task.get("projectName") or ""),
        "due_date": _date(task.get("dueDate")),
        "assignee_names": _assignee_names(task, included),
        "task_bucket": task_bucket,
        "updated_at_remote": _ts(task.get("updatedAt") or task.get("lastChangedOn")),
        "synced_at": synced_at,
        "raw": task,
    }
    logger.debug("operation=teamwork_map_task site_id=%s task_id=%s bucket=%s", site_id, row["task_id"], task_bucket)
    return row


def map_person(*, site_id: str, person: dict[str, Any], included: dict[str, Any], synced_at: str) -> dict[str, Any] | None:
    if person.get("deleted") or person.get("isClientUser"):
        return None
    company_id = _ref_id(person.get("companyId") or person.get("company"))
    companies = included.get("companies") if isinstance(included.get("companies"), dict) else {}
    company = companies.get(str(company_id)) if company_id is not None else None
    row = {
        "site_id": site_id,
        "person_id": _ref_id(person.get("id")) or 0,
        "name": _name(person),
        "email": str(person.get("email") or ""),
        "title": str(person.get("title") or "") or None,
        "company_name": str((company or {}).get("name") or ""),
        "updated_at_remote": _ts(person.get("updatedAt") or person.get("lastChangedOn")),
        "synced_at": synced_at,
        "raw": person,
    }
    logger.debug("operation=teamwork_map_person site_id=%s person_id=%s", site_id, row["person_id"])
    return row


def map_timelog(*, site_id: str, timelog: dict[str, Any], included: dict[str, Any], synced_at: str) -> dict[str, Any]:
    project_id = _ref_id(timelog.get("projectId") or timelog.get("project"))
    user_id = _ref_id(timelog.get("userId") or timelog.get("user"))
    projects = included.get("projects") if isinstance(included.get("projects"), dict) else {}
    users = included.get("users") if isinstance(included.get("users"), dict) else {}
    person = users.get(str(user_id)) if user_id is not None else None
    project = projects.get(str(project_id)) if project_id is not None else None
    row = {
        "site_id": site_id,
        "timelog_id": _ref_id(timelog.get("id")) or 0,
        "project_id": project_id,
        "project_name": str((project or {}).get("name") or timelog.get("projectName") or ""),
        "user_id": user_id,
        "user_name": _name(person or {}) or str(timelog.get("userName") or ""),
        "minutes": _int(timelog.get("minutes")),
        "billable": _bool(timelog.get("billable")),
        "time_logged": _ts(timelog.get("timeLogged") or timelog.get("date")),
        "updated_at_remote": _ts(timelog.get("updatedAt") or timelog.get("lastChangedOn")),
        "synced_at": synced_at,
        "raw": timelog,
    }
    logger.debug("operation=teamwork_map_timelog site_id=%s timelog_id=%s", site_id, row["timelog_id"])
    return row


def map_milestone(*, site_id: str, milestone: dict[str, Any], included: dict[str, Any], synced_at: str) -> dict[str, Any]:
    project_id = _ref_id(milestone.get("projectId") or milestone.get("project"))
    projects = included.get("projects") if isinstance(included.get("projects"), dict) else {}
    project = projects.get(str(project_id)) if project_id is not None else None
    row = {
        "site_id": site_id,
        "milestone_id": _ref_id(milestone.get("id")) or 0,
        "name": str(milestone.get("name") or "Untitled milestone"),
        "status": str(milestone.get("status") or ""),
        "project_id": project_id,
        "project_name": str((project or {}).get("name") or milestone.get("projectName") or ""),
        "due_date": _date(milestone.get("deadline") or milestone.get("dueDate")),
        "progress_pct": _int(milestone.get("percentageComplete")) if milestone.get("percentageComplete") is not None else None,
        "updated_at_remote": _ts(milestone.get("updatedAt") or milestone.get("lastChangedOn")),
        "synced_at": synced_at,
        "raw": milestone,
    }
    logger.debug("operation=teamwork_map_milestone site_id=%s milestone_id=%s", site_id, row["milestone_id"])
    return row
