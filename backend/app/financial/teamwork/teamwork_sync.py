"""Teamwork backfill and nightly snapshot sync orchestration."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.financial.teamwork import client
from app.financial.teamwork.errors import TeamworkError
from app.financial.teamwork.teamwork_map import (
    map_milestone,
    map_person,
    map_project,
    map_task,
    map_timelog,
    site_id_from_base_url,
)
from app.financial.teamwork.teamwork_panels_from_db import build_overview
from app.financial.teamwork.teamwork_repository import (
    finish_sync_run,
    get_panel_cache,
    get_sync_state,
    insert_sync_run,
    prune_snapshot_rows,
    release_lease,
    try_acquire_lease,
    upsert_milestones,
    upsert_panel_cache,
    upsert_people,
    upsert_projects,
    upsert_sync_state,
    upsert_tasks,
    upsert_timelogs,
)

logger = logging.getLogger(__name__)


class LeaseHeld(RuntimeError):
    pass


def _site_id() -> str:
    return site_id_from_base_url(settings.teamwork_base_url)


def _time_window_year_start(started: datetime) -> str:
    return f"{started.year}-01-01"


def _sync_tasks(*, site_id: str, synced_at: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for bucket, task_filter in (("overdue", "overdue"), ("upcoming", "within14")):
        params = {"taskFilter": task_filter}
        rows, included = client.list_tasks(params)
        mapped = [
            map_task(
                site_id=site_id,
                task=task,
                included=included,
                task_bucket=bucket,
                synced_at=synced_at,
            )
            for task in rows
        ]
        counts[bucket] = upsert_tasks(mapped)
    return counts


def _sync_projects(*, site_id: str, synced_at: str) -> int:
    rows, included = client.list_projects()
    mapped = []
    for row in rows:
        summary = None
        try:
            project_id_raw = row.get("id")
            if project_id_raw is not None:
                summary = client.get_project_summary(int(project_id_raw))
        except TeamworkError:
            logger.warning(
                "operation=teamwork_get_project_summary_failed site_id=%s project=%s",
                site_id,
                row.get("id"),
            )
            summary = None

        item = map_project(
            site_id=site_id,
            project=row,
            included=included,
            synced_at=synced_at,
            summary=summary,
        )
        if str(item.get("status") or "").lower() == "completed":
            logger.info(
                "operation=teamwork_skip_completed_project site_id=%s project_id=%s",
                site_id,
                item.get("project_id"),
            )
            continue
        mapped.append(item)
    return upsert_projects(mapped)


def _sync_people(*, site_id: str, synced_at: str) -> int:
    rows, included = client.list_people()
    mapped = [
        mapped_row
        for row in rows
        if (mapped_row := map_person(site_id=site_id, person=row, included=included, synced_at=synced_at)) is not None
    ]
    return upsert_people(mapped)


def _sync_timelogs(*, site_id: str, synced_at: str, started: datetime) -> int:
    params = {
        "startDate": _time_window_year_start(started),
        "endDate": started.date().isoformat(),
    }
    rows, included = client.list_timelogs(params)
    mapped = [map_timelog(site_id=site_id, timelog=row, included=included, synced_at=synced_at) for row in rows]
    return upsert_timelogs(mapped)


def _sync_milestones(*, site_id: str, synced_at: str) -> int:
    rows, included = client.list_milestones()
    mapped = [map_milestone(site_id=site_id, milestone=row, included=included, synced_at=synced_at) for row in rows]
    return upsert_milestones(mapped)


def _fetch_snapshot(*, site_id: str, started: datetime, synced_at: str) -> dict[str, int]:
    logger.info("operation=teamwork_fetch_snapshot site_id=%s", site_id)
    task_counts = _sync_tasks(site_id=site_id, synced_at=synced_at)
    counts = {
        "projects": _sync_projects(site_id=site_id, synced_at=synced_at),
        "people": _sync_people(site_id=site_id, synced_at=synced_at),
        "timelogs": _sync_timelogs(site_id=site_id, synced_at=synced_at, started=started),
        "milestones": _sync_milestones(site_id=site_id, synced_at=synced_at),
        "overdue_tasks": task_counts["overdue"],
        "upcoming_tasks": task_counts["upcoming"],
    }
    prune_snapshot_rows(site_id, synced_at)
    return counts


def _write_panel_cache(site_id: str, *, started: datetime, computed_at: str) -> None:
    payload = build_overview(site_id, as_of=started.date().isoformat(), computed_at=computed_at)
    upsert_panel_cache(site_id, payload, started.date().isoformat(), computed_at)


def _run_backfill(*, site_id: str, started: datetime, run_id: str) -> dict[str, int]:
    synced_at = started.isoformat()
    logger.info("operation=teamwork_run_backfill site_id=%s run_id=%s status=started", site_id, run_id)
    upsert_sync_state(site_id, {"last_started_at": synced_at, "last_mode": "backfill"})
    counts = _fetch_snapshot(site_id=site_id, started=started, synced_at=synced_at)
    computed_at = datetime.now(timezone.utc).isoformat()
    _write_panel_cache(site_id, started=started, computed_at=computed_at)
    now = datetime.now(timezone.utc).isoformat()
    upsert_sync_state(
        site_id,
        {
            "backfill_completed_at": now,
            "last_success_at": now,
            "last_error": None,
            "last_mode": "backfill",
        },
    )
    logger.info("operation=teamwork_run_backfill site_id=%s run_id=%s status=success", site_id, run_id)
    return counts


def _run_nightly(*, site_id: str, started: datetime, run_id: str) -> dict[str, int]:
    synced_at = started.isoformat()
    logger.info("operation=teamwork_run_nightly site_id=%s run_id=%s", site_id, run_id)
    upsert_sync_state(site_id, {"last_started_at": synced_at, "last_mode": "nightly"})
    counts = _fetch_snapshot(site_id=site_id, started=started, synced_at=synced_at)
    computed_at = datetime.now(timezone.utc).isoformat()
    _write_panel_cache(site_id, started=started, computed_at=computed_at)
    now = datetime.now(timezone.utc).isoformat()
    upsert_sync_state(
        site_id,
        {
            "last_success_at": now,
            "last_error": None,
            "last_mode": "nightly",
        },
    )
    logger.info("operation=teamwork_run_nightly site_id=%s run_id=%s status=success", site_id, run_id)
    return counts


def _record_sync_failure(*, site_id: str, run_id: str | None, mode: str, exc: Exception, duration_ms: int) -> None:
    logger.exception(
        "operation=teamwork_run_sync run_id=%s mode=%s status=failed duration_ms=%s error_type=%s",
        run_id,
        mode,
        duration_ms,
        type(exc).__name__,
    )
    if run_id:
        try:
            finish_sync_run(run_id, "failed", error=str(exc)[:500])
        except Exception:
            logger.exception("operation=teamwork_finish_sync_run_cleanup run_id=%s status=failed", run_id)
    try:
        upsert_sync_state(site_id, {"last_error": str(exc)[:500]})
    except Exception:
        logger.exception("operation=teamwork_upsert_sync_state_cleanup site_id=%s status=failed", site_id)


def run_sync(mode: str = "auto") -> dict[str, str]:
    if not settings.teamwork_configured:
        raise TeamworkError("Teamwork credentials are not configured")
    site_id = _site_id()
    owner = f"teamwork-sync-{uuid4()}"
    t0 = time.monotonic()
    if not try_acquire_lease(site_id, owner):
        logger.warning("operation=teamwork_run_sync site_id=%s status=lease_held", site_id)
        raise LeaseHeld("Teamwork sync lease is held")

    started = datetime.now(timezone.utc)
    run_id: str | None = None
    resolved_mode = mode
    try:
        state = get_sync_state(site_id) or {}
        if mode == "auto":
            resolved_mode = "nightly" if state.get("backfill_completed_at") else "backfill"
        run_id = insert_sync_run(
            {
                "site_id": site_id,
                "mode": resolved_mode,
                "status": "running",
                "started_at": started.isoformat(),
            }
        )
        if resolved_mode == "backfill":
            counts = _run_backfill(site_id=site_id, started=started, run_id=run_id)
        elif resolved_mode == "nightly":
            counts = _run_nightly(site_id=site_id, started=started, run_id=run_id)
        else:
            raise ValueError(f"Unknown sync mode: {mode}")
        finish_sync_run(run_id, "success", entities_upserted=counts)
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "operation=teamwork_run_sync run_id=%s mode=%s status=success duration_ms=%s",
            run_id,
            resolved_mode,
            duration_ms,
        )
        return {"status": "success", "mode": resolved_mode, "run_id": run_id}
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _record_sync_failure(site_id=site_id, run_id=run_id, mode=resolved_mode, exc=exc, duration_ms=duration_ms)
        raise
    finally:
        try:
            release_lease(site_id, owner)
        except Exception:
            logger.exception("operation=teamwork_release_lease_cleanup site_id=%s status=failed", site_id)


def overview_from_cache() -> dict[str, Any]:
    site_id = _site_id()
    state = get_sync_state(site_id) or {}
    cache = get_panel_cache(site_id)
    if cache is None:
        return {
            "connected": settings.teamwork_configured,
            "generated_at": None,
            "as_of": None,
            "cache_ttl_seconds": 0,
            "errors": {"overview": "no snapshot available"},
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
                "period_start": "",
                "period_end": "",
                "total_minutes": 0,
                "billable_minutes": 0,
                "by_person": [],
                "by_project": [],
            },
            "synced_at": state.get("last_success_at"),
            "sync_status": "backfill_pending" if not state.get("backfill_completed_at") else "missing",
        }
    payload = cache.get("payload") or {}
    return {
        **payload,
        "as_of": cache.get("as_of"),
        "generated_at": cache.get("computed_at"),
        "synced_at": state.get("last_success_at") or cache.get("computed_at"),
        "sync_status": "failed" if state.get("last_error") and state.get("last_success_at") else "ok",
    }
