"""Supabase persistence for the QuickBooks mirror and OAuth tokens."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.financial.qb_map import (
    ENTITY_TABLES,
    entity_row,
    is_qbo_deleted,
    payment_links,
    purchase_lines,
)
from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_LIST_PAGE_SIZE = 1000


def get_oauth_tokens(realm_id: str) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_oauth_tokens")
        .select("*")
        .eq("realm_id", realm_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    row = rows[0] if rows else None
    logger.info(
        "operation=get_oauth_tokens realm_id=%s found=%s",
        realm_id,
        row is not None,
    )
    return row


def upsert_oauth_tokens(realm_id: str, row: dict[str, Any]) -> None:
    payload = {
        **row,
        "realm_id": realm_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (
        _get_client()
        .table("qb_oauth_tokens")
        .upsert(payload, on_conflict="realm_id")
        .execute()
    )
    logger.info("operation=upsert_oauth_tokens realm_id=%s", realm_id)


def _rows(data: Any) -> list[dict[str, Any]]:
    if not data:
        return []
    return data if isinstance(data, list) else [data]


def _first(data: Any) -> dict[str, Any] | None:
    rows = _rows(data)
    return rows[0] if rows else None


def _upsert_batches(
    table: str,
    rows: list[dict[str, Any]],
    *,
    on_conflict: str,
) -> None:
    client = _get_client()
    for start in range(0, len(rows), _BATCH_SIZE):
        client.table(table).upsert(
            rows[start : start + _BATCH_SIZE],
            on_conflict=on_conflict,
        ).execute()


def replace_purchase_children(
    realm_id: str,
    purchase_ids: list[str],
    lines: list[dict[str, Any]],
) -> None:
    if not purchase_ids:
        return
    (
        _get_client()
        .table("qb_purchase_lines")
        .delete()
        .eq("realm_id", realm_id)
        .in_("purchase_id", purchase_ids)
        .execute()
    )
    _upsert_batches(
        "qb_purchase_lines",
        lines,
        on_conflict="realm_id,purchase_id,line_id",
    )
    logger.info(
        "operation=replace_purchase_children realm_id=%s parent_count=%s line_count=%s",
        realm_id,
        len(purchase_ids),
        len(lines),
    )


def replace_payment_links(
    realm_id: str,
    payment_ids: list[str],
    links: list[dict[str, Any]],
) -> None:
    if not payment_ids:
        return
    (
        _get_client()
        .table("qb_txn_links")
        .delete()
        .eq("realm_id", realm_id)
        .eq("from_type", "Payment")
        .in_("from_id", payment_ids)
        .execute()
    )
    _upsert_batches(
        "qb_txn_links",
        links,
        on_conflict="realm_id,from_type,from_id,to_type,to_id",
    )
    logger.info(
        "operation=replace_payment_links realm_id=%s parent_count=%s link_count=%s",
        realm_id,
        len(payment_ids),
        len(links),
    )


def upsert_entities(
    realm_id: str,
    entity: str,
    payloads: list[dict[str, Any]],
    *,
    synced_at: str,
) -> int:
    table = ENTITY_TABLES.get(entity)
    if table is None:
        raise ValueError(f"Unsupported QuickBooks entity: {entity}")
    if not payloads:
        return 0

    mapped = [
        entity_row(realm_id, entity, payload, synced_at=synced_at)
        for payload in payloads
    ]
    _upsert_batches(table, mapped, on_conflict="realm_id,qbo_id")

    if entity == "Purchase":
        parent_ids = [
            str(payload["Id"])
            for payload in payloads
            if payload.get("Id") is not None
        ]
        lines = [
            line
            for payload in payloads
            if not is_qbo_deleted(payload)
            for line in purchase_lines(realm_id, payload)
        ]
        replace_purchase_children(realm_id, parent_ids, lines)
    elif entity == "Payment":
        parent_ids = [
            str(payload["Id"])
            for payload in payloads
            if payload.get("Id") is not None
        ]
        links = [
            link
            for payload in payloads
            if not is_qbo_deleted(payload)
            for link in payment_links(realm_id, payload)
        ]
        replace_payment_links(realm_id, parent_ids, links)

    logger.info(
        "operation=upsert_entities realm_id=%s entity=%s entity_count=%s",
        realm_id,
        entity,
        len(mapped),
    )
    return len(mapped)


def get_sync_state(realm_id: str) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_sync_state")
        .select("*")
        .eq("realm_id", realm_id)
        .limit(1)
        .execute()
    )
    return _first(result.data)


def upsert_sync_state(realm_id: str, fields: dict[str, Any]) -> None:
    _get_client().table("qb_sync_state").upsert(
        {**fields, "realm_id": realm_id},
        on_conflict="realm_id",
    ).execute()
    logger.info("operation=upsert_sync_state realm_id=%s", realm_id)


def _parse_datetime(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def try_acquire_lease(
    realm_id: str,
    owner: str,
    ttl_seconds: int = 900,
) -> bool:
    now = datetime.now(timezone.utc)
    state = get_sync_state(realm_id) or {}
    current_owner = state.get("lease_owner")
    expires_at = state.get("lease_expires_at")
    if (
        current_owner
        and current_owner != owner
        and expires_at
        and _parse_datetime(expires_at) > now
    ):
        logger.info(
            "operation=try_acquire_lease realm_id=%s acquired=false",
            realm_id,
        )
        return False
    upsert_sync_state(
        realm_id,
        {
            "lease_owner": owner,
            "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        },
    )
    logger.info(
        "operation=try_acquire_lease realm_id=%s acquired=true",
        realm_id,
    )
    return True


def release_lease(realm_id: str, owner: str) -> None:
    state = get_sync_state(realm_id) or {}
    if state.get("lease_owner") != owner:
        logger.warning(
            "operation=release_lease realm_id=%s released=false reason=owner_mismatch",
            realm_id,
        )
        return
    upsert_sync_state(
        realm_id,
        {"lease_owner": None, "lease_expires_at": None},
    )
    logger.info("operation=release_lease realm_id=%s released=true", realm_id)


def insert_sync_run(row: dict[str, Any]) -> str:
    result = _get_client().table("qb_sync_runs").insert(row).execute()
    inserted = _first(result.data)
    run_id = (inserted or {}).get("id")
    if not run_id:
        raise RuntimeError("qb_sync_runs insert returned no id")
    logger.info(
        "operation=insert_sync_run realm_id=%s run_id=%s",
        row.get("realm_id"),
        run_id,
    )
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
    (
        _get_client()
        .table("qb_sync_runs")
        .update(fields)
        .eq("id", run_id)
        .execute()
    )
    logger.info(
        "operation=finish_sync_run run_id=%s status=%s",
        run_id,
        status,
    )


def get_backfill_progress(
    realm_id: str,
    entity: str,
) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_backfill_progress")
        .select("*")
        .eq("realm_id", realm_id)
        .eq("entity", entity)
        .limit(1)
        .execute()
    )
    return _first(result.data)


def clear_backfill_progress(realm_id: str) -> None:
    (
        _get_client()
        .table("qb_backfill_progress")
        .delete()
        .eq("realm_id", realm_id)
        .execute()
    )
    logger.info("operation=clear_backfill_progress realm_id=%s", realm_id)


def upsert_backfill_progress(
    realm_id: str,
    entity: str,
    startposition: int,
    completed: bool,
) -> None:
    _get_client().table("qb_backfill_progress").upsert(
        {
            "realm_id": realm_id,
            "entity": entity,
            "startposition": startposition,
            "completed": completed,
        },
        on_conflict="realm_id,entity",
    ).execute()
    logger.info(
        "operation=upsert_backfill_progress realm_id=%s entity=%s "
        "startposition=%s completed=%s",
        realm_id,
        entity,
        startposition,
        completed,
    )


def upsert_report_snapshot(row: dict[str, Any]) -> None:
    _get_client().table("qb_report_snapshots").upsert(
        row,
        on_conflict="realm_id,report_name,year,params_hash",
    ).execute()
    logger.info(
        "operation=upsert_report_snapshot realm_id=%s report_name=%s year=%s",
        row.get("realm_id"),
        row.get("report_name"),
        row.get("year"),
    )


def get_report_snapshot(
    realm_id: str,
    report_name: str,
    year: int,
    params_hash: str,
) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_report_snapshots")
        .select("*")
        .eq("realm_id", realm_id)
        .eq("report_name", report_name)
        .eq("year", year)
        .eq("params_hash", params_hash)
        .limit(1)
        .execute()
    )
    return _first(result.data)


def upsert_company_info(row: dict[str, Any]) -> None:
    _get_client().table("qb_company_info").upsert(
        row,
        on_conflict="realm_id",
    ).execute()
    logger.info(
        "operation=upsert_company_info realm_id=%s",
        row.get("realm_id"),
    )


def get_company_info(realm_id: str) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_company_info")
        .select("*")
        .eq("realm_id", realm_id)
        .limit(1)
        .execute()
    )
    row = _first(result.data)
    logger.info(
        "operation=get_company_info realm_id=%s found=%s",
        realm_id,
        row is not None,
    )
    return row


def upsert_panel_cache(
    realm_id: str,
    year: int,
    payload: dict[str, Any],
    as_of: str,
    computed_at: str,
) -> None:
    _get_client().table("qb_panel_cache").upsert(
        {
            "realm_id": realm_id,
            "year": year,
            "payload": payload,
            "as_of": as_of,
            "computed_at": computed_at,
        },
        on_conflict="realm_id,year",
    ).execute()
    logger.info(
        "operation=upsert_panel_cache realm_id=%s year=%s",
        realm_id,
        year,
    )


def get_panel_cache(realm_id: str, year: int) -> dict[str, Any] | None:
    result = (
        _get_client()
        .table("qb_panel_cache")
        .select("*")
        .eq("realm_id", realm_id)
        .eq("year", year)
        .limit(1)
        .execute()
    )
    return _first(result.data)


_FILTER_OPERATORS = {
    "eq": "eq",
    "neq": "neq",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "in": "in_",
    "is": "is_",
}


def _filtered_query(table: str, realm_id: str, filters: dict[str, Any]):
    query = _get_client().table(table).select("*").eq(
        "realm_id",
        realm_id,
    )
    for key, value in filters.items():
        if value is None:
            continue
        column, separator, operator = key.rpartition("__")
        if not separator:
            column, operator = key, "eq"
        method_name = _FILTER_OPERATORS.get(operator)
        if method_name is None:
            raise ValueError(f"Unsupported repository filter operator: {operator}")
        query = getattr(query, method_name)(column, value)
    return query


def _list_rows(
    table: str,
    realm_id: str,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    offset = 0
    rows: list[dict[str, Any]] = []
    while True:
        result = (
            _filtered_query(table, realm_id, filters)
            .range(offset, offset + _LIST_PAGE_SIZE - 1)
            .execute()
        )
        page = _rows(result.data)
        rows.extend(page)
        if len(page) < _LIST_PAGE_SIZE:
            break
        offset += _LIST_PAGE_SIZE
    logger.debug(
        "operation=list_qb_rows table=%s realm_id=%s row_count=%s",
        table,
        realm_id,
        len(rows),
    )
    return rows


def list_invoices(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_invoices", realm_id, filters)


def list_bills(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_bills", realm_id, filters)


def list_payments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_payments", realm_id, filters)


def list_purchases(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_purchases", realm_id, filters)


def list_purchase_lines(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_purchase_lines", realm_id, filters)


def list_txn_links(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_txn_links", realm_id, filters)


def list_purchase_orders(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_purchase_orders", realm_id, filters)


def list_bill_payments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_bill_payments", realm_id, filters)


def list_credit_memos(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_credit_memos", realm_id, filters)


def list_customers(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_customers", realm_id, filters)


def list_classes(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_classes", realm_id, filters)


def list_departments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return _list_rows("qb_departments", realm_id, filters)
