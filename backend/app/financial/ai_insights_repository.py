"""Supabase persistence for nightly AI insights."""

from __future__ import annotations

import logging
from typing import Any

from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)


def upsert_insight(
    *,
    source: str,
    scope_key: str,
    as_of: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    provider: str | None,
    model: str | None,
    status: str,
    error: str | None = None,
) -> None:
    _get_client().table("ai_insights").upsert(
        {
            "source": source,
            "scope_key": scope_key,
            "as_of": as_of,
            "payload": payload,
            "evidence": evidence,
            "provider": provider,
            "model": model,
            "status": status,
            "error": (error or "")[:500] or None,
        },
        on_conflict="source,scope_key,as_of",
    ).execute()
    logger.info(
        "operation=upsert_insight source=%s scope_key=%s as_of=%s status=%s",
        source,
        scope_key,
        as_of,
        status,
    )


def get_latest_insight(source: str, scope_key: str) -> dict[str, Any] | None:
    """Newest successful row, or None. Failed nights are skipped, so a bad run
    degrades to yesterday's brief rather than an empty panel."""
    result = (
        _get_client()
        .table("ai_insights")
        .select("*")
        .eq("source", source)
        .eq("scope_key", scope_key)
        .eq("status", "ok")
        .order("as_of", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    row = rows[0] if rows else None
    logger.info(
        "operation=get_latest_insight source=%s scope_key=%s found=%s",
        source,
        scope_key,
        row is not None,
    )
    return row


def get_insight(source: str, scope_key: str, as_of: str) -> dict[str, Any] | None:
    """One successful row for an exact as-of date (e.g. Agency weekly Monday key)."""
    result = (
        _get_client()
        .table("ai_insights")
        .select("*")
        .eq("source", source)
        .eq("scope_key", scope_key)
        .eq("as_of", as_of)
        .eq("status", "ok")
        .limit(1)
        .execute()
    )
    rows = result.data or []
    row = rows[0] if rows else None
    logger.info(
        "operation=get_insight source=%s scope_key=%s as_of=%s found=%s",
        source,
        scope_key,
        as_of,
        row is not None,
    )
    return row
