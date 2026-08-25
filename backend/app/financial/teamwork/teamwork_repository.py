"""Supabase persistence for the Teamwork mirror tables."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_LIST_PAGE_SIZE = 1000
_SNAPSHOT_TABLES = (
    "teamwork_projects",
    "teamwork_tasks",
    "teamwork_people",
    "teamwork_time_entries",
    "teamwork_milestones",
)


def _rows(data: Any) -> list[dict[str, Any]]:
    if not data:
        return []
    return data if isinstance(data, list) else [data]


def _first(data: Any) -> dict[str, Any] | None:
    rows = _rows(data)
    return rows[0] if rows else None


def _upsert_batches(table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> int:
    if not rows:
        return 0
    client = _get_client()
    total = 0
    for start in range(0, len(rows), _BATCH_SIZE):
        batch = rows[start : start + _BATCH_SIZE]
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        total += len(batch)
    logger.info("operation=teamwork_upsert table=%s row_count=%s", table, total)
    return total


def _filtered_query(table: str, site_id: str, filters: dict[str, Any]):
    query = _get_client().table(table).select("*").eq("site_id", site_id)
    operators = {
        "eq": "eq",
        "neq": "neq",
        "gt": "gt",
        "gte": "gte",
        "lt": "lt",
        "lte": "lte",
        "in": "in_",
        "is": "is_",
    }
    for key, value in filters.items():
        if value is None:
            continue
        column, separator, operator = key.rpartition("__")
        if not separator:
            column, operator = key, "eq"
        method_name = operators[operator]
        query = getattr(query, method_name)(column, value)
    return query


def _list_rows(table: str, site_id: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    offset = 0
    rows: list[dict[str, Any]] = []
    while True:
        result = _filtered_query(table, site_id, filters).range(offset, offset + _LIST_PAGE_SIZE - 1).execute()
        page = _rows(result.data)
        rows.extend(page)
        if len(page) < _LIST_PAGE_SIZE:
            break
        offset += _LIST_PAGE_SIZE
    logger.debug("operation=teamwork_list table=%s site_id=%s row_count=%s", table, site_id, len(rows))
    return rows


def upsert_projects(rows: list[dict[str, Any]]) -> int:
    return _upsert_batches("teamwork_projects", rows, on_conflict="site_id,project_id")


def upsert_tasks(rows: list[dict[str, Any]]) -> int:
    return _upsert_batches("teamwork_tasks", rows, on_conflict="site_id,task_id")


def upsert_people(rows: list[dict[str, Any]]) -> int:
    return _upsert_batches("teamwork_people", rows, on_conflict="site_id,person_id")


def upsert_timelogs(rows: list[dict[str, Any]]) -> int:
    return _upsert_batches("teamwork_time_entries", rows, on_conflict="site_id,timelog_id")


def upsert_milestones(rows: list[dict[str, Any]]) -> int:
    return _upsert_batches("teamwork_milestones", rows, on_conflict="site_id,milestone_id")


def upsert_capacity_snapshots(site_id: str, as_of: str, rows: list[dict[str, Any]]) -> int:
    payload = [{**row, "site_id": site_id, "as_of": as_of} for row in rows]
    return _upsert_batches(
        "teamwork_capacity_snapshots", payload, on_conflict="site_id,as_of,person_id"
    )


def prune_snapshot_rows(site_id: str, synced_at: str) -> None:
    """Remove mirror rows absent from a completed full Teamwork snapshot."""
    client = _get_client()
    for table in _SNAPSHOT_TABLES:
        client.table(table).delete().eq("site_id", site_id).lt("synced_at", synced_at).execute()
    logger.info("operation=teamwork_prune_snapshot site_id=%s", site_id)


def list_projects(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("teamwork_projects", site_id, filters)


def list_tasks(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("teamwork_tasks", site_id, filters)


def list_people(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("teamwork_people", site_id, filters)


def list_timelogs(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("teamwork_time_entries", site_id, filters)


def list_milestones(site_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("teamwork_milestones", site_id, filters)


def list_capacity_snapshots(site_id: str, since: str | None = None) -> list[dict[str, Any]]:
    filters = {"as_of__gte": since} if since else {}
    return _list_rows("teamwork_capacity_snapshots", site_id, filters)


def get_panel_cache(site_id: str) -> dict[str, Any] | None:
    result = _get_client().table("teamwork_panel_cache").select("*").eq("site_id", site_id).limit(1).execute()
    return _first(result.data)


def upsert_panel_cache(site_id: str, payload: dict[str, Any], as_of: str, computed_at: str) -> None:
    _get_client().table("teamwork_panel_cache").upsert(
        {
            "site_id": site_id,
            "payload": payload,
            "as_of": as_of,
            "computed_at": computed_at,
        },
        on_conflict="site_id",
    ).execute()
    logger.info("operation=teamwork_upsert_panel_cache site_id=%s", site_id)


def get_sync_state(site_id: str) -> dict[str, Any] | None:
    result = _get_client().table("teamwork_sync_state").select("*").eq("site_id", site_id).limit(1).execute()
    return _first(result.data)


def upsert_sync_state(site_id: str, fields: dict[str, Any]) -> None:
    _get_client().table("teamwork_sync_state").upsert(
        {**fields, "site_id": site_id},
        on_conflict="site_id",
    ).execute()
    logger.info("operation=teamwork_upsert_sync_state site_id=%s", site_id)


def try_acquire_lease(site_id: str, owner: str, ttl_seconds: int = 900) -> bool:
    state = get_sync_state(site_id) or {}
    now = datetime.now(timezone.utc)
    expires_at = state.get("lease_expires_at")
    if state.get("lease_owner") and state.get("lease_owner") != owner and expires_at:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > now:
            logger.info("operation=teamwork_try_acquire_lease site_id=%s acquired=false", site_id)
            return False
    upsert_sync_state(
        site_id,
        {
            "lease_owner": owner,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        },
    )
    logger.info("operation=teamwork_try_acquire_lease site_id=%s acquired=true", site_id)
    return True


def release_lease(site_id: str, owner: str) -> None:
    state = get_sync_state(site_id) or {}
    if state.get("lease_owner") != owner:
        logger.warning("operation=teamwork_release_lease site_id=%s released=false reason=owner_mismatch", site_id)
        return
    upsert_sync_state(site_id, {"lease_owner": None, "lease_expires_at": None})
    logger.info("operation=teamwork_release_lease site_id=%s released=true", site_id)


def insert_sync_run(row: dict[str, Any]) -> str:
    result = _get_client().table("teamwork_sync_runs").insert(row).execute()
    inserted = _first(result.data)
    run_id = (inserted or {}).get("id")
    if not run_id:
        raise RuntimeError("teamwork_sync_runs insert returned no id")
    logger.info("operation=teamwork_insert_sync_run site_id=%s run_id=%s", row.get("site_id"), run_id)
    return str(run_id)


def finish_sync_run(
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    entities_upserted: dict[str, int] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": error,
    }
    if entities_upserted is not None:
        fields["entities_upserted"] = entities_upserted
    _get_client().table("teamwork_sync_runs").update(fields).eq("id", run_id).execute()
    logger.info("operation=teamwork_finish_sync_run run_id=%s status=%s", run_id, status)
