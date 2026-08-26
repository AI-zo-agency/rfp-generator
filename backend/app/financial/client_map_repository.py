"""Supabase persistence for agency TW↔QB client mapping."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)

_CLIENT_MAP_TABLE = "client_map"
_JOB_OVERRIDE_TABLE = "client_map_job_override"


def _rows(data: Any) -> list[dict[str, Any]]:
    if not data:
        return []
    return data if isinstance(data, list) else [data]


def _first(data: Any) -> dict[str, Any] | None:
    rows = _rows(data)
    return rows[0] if rows else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_client_map(
    *,
    confidence: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    query = _get_client().table(_CLIENT_MAP_TABLE).select("*")
    if confidence:
        query = query.eq("link_confidence", confidence)
    if status:
        query = query.eq("status", status)
    if q:
        needle = f"%{q.strip()}%"
        query = query.or_(f"client_name.ilike.{needle},tag_code.ilike.{needle}")
    result = query.order("tag_code").order("client_name").execute()
    rows = _rows(result.data)
    logger.info(
        "operation=list_client_map confidence=%s status=%s q=%s row_count=%s",
        confidence,
        status,
        q,
        len(rows),
    )
    return rows


def get_client_map(row_id: str) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table(_CLIENT_MAP_TABLE)
        .select("*")
        .eq("id", row_id)
        .limit(1)
        .execute()
    )
    row = _first(result.data)
    logger.info("operation=get_client_map row_id=%s found=%s", row_id, row is not None)
    return row


def insert_client_map(payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        **payload,
        "link_confidence": payload.get("link_confidence") or "unmatched",
        "updated_at": _now_iso(),
    }
    result = _get_client().table(_CLIENT_MAP_TABLE).insert(row).execute()
    inserted = _first(result.data)
    logger.info(
        "operation=insert_client_map row_id=%s tag_code=%s",
        (inserted or {}).get("id"),
        row.get("tag_code"),
    )
    return inserted or row


def update_client_map(row_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    payload = {**patch, "updated_at": _now_iso()}
    result = (
        _get_client()
        .table(_CLIENT_MAP_TABLE)
        .update(payload)
        .eq("id", row_id)
        .execute()
    )
    row = _first(result.data)
    logger.info("operation=update_client_map row_id=%s found=%s", row_id, row is not None)
    return row


def delete_client_map(row_id: str) -> None:
    _get_client().table(_CLIENT_MAP_TABLE).delete().eq("id", row_id).execute()
    logger.info("operation=delete_client_map row_id=%s", row_id)


def list_by_tag(tag_code: str) -> list[dict[str, Any]]:
    result = (
        _get_client()
        .table(_CLIENT_MAP_TABLE)
        .select("*")
        .ilike("tag_code", tag_code)
        .order("client_name")
        .execute()
    )
    rows = _rows(result.data)
    logger.info("operation=list_by_tag tag_code=%s row_count=%s", tag_code, len(rows))
    return rows


def list_job_overrides(*, site_id: str | None = None) -> list[dict[str, Any]]:
    query = _get_client().table(_JOB_OVERRIDE_TABLE).select("*")
    if site_id:
        query = query.eq("site_id", site_id)
    result = query.order("site_id").order("project_id").execute()
    rows = _rows(result.data)
    logger.info(
        "operation=list_job_overrides site_id=%s row_count=%s",
        site_id,
        len(rows),
    )
    return rows


def get_job_override(site_id: str, project_id: int) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table(_JOB_OVERRIDE_TABLE)
        .select("*")
        .eq("site_id", site_id)
        .eq("project_id", project_id)
        .limit(1)
        .execute()
    )
    row = _first(result.data)
    logger.info(
        "operation=get_job_override site_id=%s project_id=%s found=%s",
        site_id,
        project_id,
        row is not None,
    )
    return row


def upsert_job_override(payload: dict[str, Any]) -> dict[str, Any]:
    row = {**payload, "updated_at": _now_iso()}
    result = (
        _get_client()
        .table(_JOB_OVERRIDE_TABLE)
        .upsert(row, on_conflict="site_id,project_id")
        .execute()
    )
    upserted = _first(result.data)
    logger.info(
        "operation=upsert_job_override site_id=%s project_id=%s row_id=%s",
        row.get("site_id"),
        row.get("project_id"),
        (upserted or {}).get("id"),
    )
    return upserted or row


def delete_job_override(row_id: str) -> None:
    _get_client().table(_JOB_OVERRIDE_TABLE).delete().eq("id", row_id).execute()
    logger.info("operation=delete_job_override row_id=%s", row_id)


def existing_tag_codes() -> list[str]:
    result = _get_client().table(_CLIENT_MAP_TABLE).select("tag_code").execute()
    seen: set[str] = set()
    codes: list[str] = []
    for row in _rows(result.data):
        code = str(row.get("tag_code") or "").strip()
        if not code:
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        codes.append(code)
    logger.info("operation=existing_tag_codes count=%s", len(codes))
    return codes
