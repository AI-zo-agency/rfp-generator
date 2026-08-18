"""Transform Teamwork V3 payloads into dashboard-owned shapes.

Teamwork remains the source of truth. This module never exposes raw V3
documents to the frontend.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Callable

from app.core.config import settings
from app.financial.teamwork import client as tw
from app.financial.teamwork.errors import TeamworkAuthError, TeamworkError, NOT_CONFIGURED_MESSAGE

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

HEALTH_LABELS = {0: "unset", 1: "bad", 2: "ok", 3: "good"}


def fetch_projects() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return tw.list_projects()


def fetch_tasks(*, task_filter: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return tw.list_tasks({"taskFilter": task_filter})


def fetch_people() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return tw.list_people()


def fetch_timelogs(*, start_date: str, end_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return tw.list_timelogs({"startDate": start_date, "endDate": end_date})


def fetch_milestones() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return tw.list_milestones()


def _sideload(included: dict[str, Any], group: str, raw_id: Any) -> dict[str, Any] | None:
    bucket = included.get(group)
    if not isinstance(bucket, dict) or raw_id in (None, ""):
        return None
    key = str(raw_id)
    row = bucket.get(key)
    return row if isinstance(row, dict) else None


def _ref_id(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("id")
    return value


def _person_name(row: dict[str, Any] | None, fallback: str = "Unknown") -> str:
    if not row:
        return fallback
    first = str(row.get("firstName") or "").strip()
    last = str(row.get("lastName") or "").strip()
    name = f"{first} {last}".strip()
    return name or str(row.get("email") or fallback)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _task_counts(project: dict[str, Any]) -> tuple[int, int, int]:
    stats = project.get("stats") if isinstance(project.get("stats"), dict) else {}
    tasks = stats.get("tasks") if isinstance(stats.get("tasks"), dict) else {}
    if not tasks:
        counts = project.get("taskCounts") if isinstance(project.get("taskCounts"), dict) else {}
        tasks = counts
    open_count = _int(tasks.get("active") or tasks.get("open") or tasks.get("incomplete"))
    completed = _int(tasks.get("completed") or tasks.get("complete"))
    late = _int(tasks.get("late") or tasks.get("overdue"))
    return open_count, completed, late


def map_projects(projects: list[dict[str, Any]], included: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project in projects:
        company_id = _ref_id(project.get("companyId") or project.get("company"))
        company = _sideload(included, "companies", company_id)
        company_name = ""
        if company:
            company_name = str(company.get("name") or "")
        if not company_name:
            company_name = str(project.get("companyName") or "")
        open_count, completed, late = _task_counts(project)
        total = open_count + completed
        progress = round((completed / total) * 100) if total else 0
        health_raw = project.get("health")
        health = HEALTH_LABELS.get(_int(health_raw, -1), "unset") if health_raw is not None else "unset"
        due = project.get("endDate") or project.get("dueDate")
        rows.append(
            {
                "id": str(project.get("id") or ""),
                "name": str(project.get("name") or "Untitled project"),
                "status": str(project.get("status") or ""),
                "health": health,
                "company_name": company_name,
                "start_date": project.get("startDate") or None,
                "due_date": due or None,
                "tasks_open": open_count,
                "tasks_completed": completed,
                "tasks_overdue": late,
                "progress_pct": progress,
            }
        )
    return rows


def _assignee_ids(task: dict[str, Any]) -> list[Any]:
    for key in ("assigneeUserIds", "responsiblePartyIds", "userIds"):
        value = task.get(key)
        if isinstance(value, list):
            return value
    assignee = task.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        return [assignee["id"]]
    if isinstance(assignee, list):
        return [_ref_id(item) for item in assignee]
    return []


def map_tasks(tasks: list[dict[str, Any]], included: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        project_id = _ref_id(task.get("projectId") or task.get("project"))
        project = _sideload(included, "projects", project_id)
        names = []
        for user_id in _assignee_ids(task):
            person = _sideload(included, "users", user_id) or _sideload(included, "people", user_id)
            names.append(_person_name(person, fallback=str(user_id)))
        rows.append(
            {
                "id": str(task.get("id") or ""),
                "name": str(task.get("name") or "Untitled task"),
                "status": str(task.get("status") or ""),
                "priority": str(task.get("priority") or "") or None,
                "due_date": task.get("dueDate") or None,
                "project_id": str(project_id or ""),
                "project_name": str((project or {}).get("name") or ""),
                "assignees": names,
            }
        )
    return rows


def map_people(people: list[dict[str, Any]], included: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for person in people:
        if person.get("deleted"):
            continue
        if person.get("isClientUser"):
            continue
        user_type = str(person.get("type") or "account").lower()
        if user_type in {"contact", "client"}:
            continue
        company_id = _ref_id(person.get("companyId") or person.get("company"))
        company = _sideload(included, "companies", company_id)
        rows.append(
            {
                "id": str(person.get("id") or ""),
                "name": _person_name(person),
                "email": str(person.get("email") or ""),
                "title": str(person.get("title") or "") or None,
                "company_name": str((company or {}).get("name") or ""),
            }
        )
    return rows


def map_milestones(milestones: list[dict[str, Any]], included: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for milestone in milestones:
        project_id = _ref_id(milestone.get("projectId") or milestone.get("project"))
        project = _sideload(included, "projects", project_id)
        progress = milestone.get("percentageComplete")
        rows.append(
            {
                "id": str(milestone.get("id") or ""),
                "name": str(milestone.get("name") or "Untitled milestone"),
                "status": str(milestone.get("status") or ""),
                "due_date": milestone.get("deadline") or milestone.get("dueDate") or None,
                "project_id": str(project_id or ""),
                "project_name": str((project or {}).get("name") or ""),
                "progress_pct": _int(progress) if progress is not None else None,
            }
        )
    return rows


def summarize_timelogs(logs: list[dict[str, Any]], included: dict[str, Any]) -> dict[str, Any]:
    total = 0
    billable = 0
    by_person: dict[str, int] = {}
    by_project: dict[str, int] = {}
    person_names: dict[str, str] = {}
    project_names: dict[str, str] = {}

    for log in logs:
        minutes = _int(log.get("minutes"))
        total += minutes
        if log.get("billable"):
            billable += minutes
        user_id = str(_ref_id(log.get("userId") or log.get("user") or "") or "")
        project_id = str(_ref_id(log.get("projectId") or log.get("project") or "") or "")
        if user_id:
            by_person[user_id] = by_person.get(user_id, 0) + minutes
            if user_id not in person_names:
                person = _sideload(included, "users", user_id) or _sideload(included, "people", user_id)
                person_names[user_id] = _person_name(person, fallback=user_id)
        if project_id:
            by_project[project_id] = by_project.get(project_id, 0) + minutes
            if project_id not in project_names:
                project = _sideload(included, "projects", project_id)
                project_names[project_id] = str((project or {}).get("name") or project_id)

    return {
        "total_minutes": total,
        "billable_minutes": billable,
        "by_person": [
            {"id": uid, "name": person_names.get(uid, uid), "minutes": minutes}
            for uid, minutes in sorted(by_person.items(), key=lambda item: item[1], reverse=True)
        ],
        "by_project": [
            {"id": pid, "name": project_names.get(pid, pid), "minutes": minutes}
            for pid, minutes in sorted(by_project.items(), key=lambda item: item[1], reverse=True)
        ],
    }


def _empty_payload(*, connected: bool, errors: dict[str, str]) -> dict[str, Any]:
    today = date.today().isoformat()
    return {
        "connected": connected,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "as_of": today,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "errors": errors,
        "summary": {
            "project_count": 0,
            "overdue_task_count": 0,
            "upcoming_task_count": 0,
            "late_milestone_count": 0,
            "hours_this_month": 0.0,
            "people_count": 0,
        },
        "projects": [],
        "overdue_tasks": [],
        "upcoming_tasks": [],
        "milestones": [],
        "people": [],
        "time": {
            "period_start": today[:8] + "01" if len(today) >= 8 else today,
            "period_end": today,
            "total_minutes": 0,
            "billable_minutes": 0,
            "by_person": [],
            "by_project": [],
        },
    }


def _run_named(name: str, fn: Callable[[], Any]) -> tuple[str, Any, TeamworkError | None]:
    try:
        return name, fn(), None
    except TeamworkError as exc:
        logger.warning("operation=teamwork_overview step=%s error=%s", name, type(exc).__name__)
        return name, None, exc


def _uncached_overview() -> dict[str, Any]:
    if not settings.teamwork_configured:
        return _empty_payload(
            connected=False,
            errors={"config": NOT_CONFIGURED_MESSAGE},
        )

    today = date.today()
    month_start = today.replace(day=1).isoformat()
    today_s = today.isoformat()

    jobs: dict[str, Callable[[], Any]] = {
        "projects": fetch_projects,
        "overdue_tasks": lambda: fetch_tasks(task_filter="overdue"),
        "upcoming_tasks": lambda: fetch_tasks(task_filter="within14"),
        "people": fetch_people,
        "timelogs": lambda: fetch_timelogs(start_date=month_start, end_date=today_s),
        "milestones": fetch_milestones,
    }

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    auth_failed = False

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_run_named, name, fn) for name, fn in jobs.items()]
        for future in as_completed(futures):
            name, value, exc = future.result()
            if exc is not None:
                errors[name] = str(exc)
                if isinstance(exc, TeamworkAuthError):
                    auth_failed = True
                    errors["auth"] = str(exc)
                continue
            results[name] = value

    if auth_failed and not results:
        payload = _empty_payload(connected=False, errors=errors)
        payload["errors"]["auth"] = errors.get("auth") or "Teamwork rejected the API key (401)"
        return payload

    projects_raw, projects_inc = results.get("projects") or ([], {})
    overdue_raw, overdue_inc = results.get("overdue_tasks") or ([], {})
    upcoming_raw, upcoming_inc = results.get("upcoming_tasks") or ([], {})
    people_raw, people_inc = results.get("people") or ([], {})
    time_raw, time_inc = results.get("timelogs") or ([], {})
    miles_raw, miles_inc = results.get("milestones") or ([], {})

    projects = map_projects(projects_raw, projects_inc)
    overdue = map_tasks(overdue_raw, overdue_inc)
    upcoming = map_tasks(upcoming_raw, upcoming_inc)
    people = map_people(people_raw, people_inc)
    milestones = map_milestones(miles_raw, miles_inc)
    time_summary = summarize_timelogs(time_raw, time_inc)

    late_milestones = [m for m in milestones if str(m.get("status") or "").lower() == "late"]
    hours = round(time_summary["total_minutes"] / 60, 1)

    connected = not auth_failed
    payload = {
        "connected": connected,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "as_of": today_s,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "errors": errors,
        "summary": {
            "project_count": len(projects),
            "overdue_task_count": len(overdue),
            "upcoming_task_count": len(upcoming),
            "late_milestone_count": len(late_milestones),
            "hours_this_month": hours,
            "people_count": len(people),
        },
        "projects": projects,
        "overdue_tasks": overdue,
        "upcoming_tasks": upcoming,
        "milestones": milestones,
        "people": people,
        "time": {
            "period_start": month_start,
            "period_end": today_s,
            **time_summary,
        },
    }
    logger.info(
        "operation=teamwork_overview connected=%s projects=%s overdue_tasks=%s hours=%s error_keys=%s",
        connected,
        len(projects),
        len(overdue),
        hours,
        sorted(errors.keys()),
    )
    return payload


def build_overview(*, force: bool = False) -> dict[str, Any]:
    if not settings.teamwork_configured:
        return _empty_payload(
            connected=False,
            errors={"config": NOT_CONFIGURED_MESSAGE},
        )
    if not force:
        with _CACHE_LOCK:
            hit = _CACHE.get("overview")
            if hit and (time.time() - hit[0]) < CACHE_TTL_SECONDS:
                logger.info("operation=teamwork_overview cache=hit")
                return hit[1]
    payload = _uncached_overview()
    with _CACHE_LOCK:
        _CACHE["overview"] = (time.time(), payload)
    return payload


def connection_status() -> dict[str, Any]:
    if not settings.teamwork_configured:
        return {
            "connected": False,
            "base_url": None,
            "reason": NOT_CONFIGURED_MESSAGE,
        }
    try:
        tw.request_json(
            tw.PROJECTS_PATH,
            params={"pageSize": 1, "skipCounts": True},
        )
        return {
            "connected": True,
            "base_url": tw.origin(),
            "reason": None,
        }
    except TeamworkAuthError as exc:
        logger.warning("operation=teamwork_status error=auth")
        return {"connected": False, "base_url": tw.origin(), "reason": str(exc)}
    except TeamworkError as exc:
        logger.warning("operation=teamwork_status error=%s", type(exc).__name__)
        return {"connected": False, "base_url": tw.origin(), "reason": str(exc)}
