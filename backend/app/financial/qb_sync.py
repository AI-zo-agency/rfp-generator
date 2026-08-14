"""QuickBooks backfill and nightly CDC orchestrator.

HTTP trigger lives in a later task. This module owns lease, ingest, cache, and cursor.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.financial.qb_map import params_hash
from app.financial.qb_panels_from_db import build_overview
from app.financial.qb_repository import (
    clear_backfill_progress,
    finish_sync_run,
    get_backfill_progress,
    get_sync_state,
    insert_sync_run,
    release_lease,
    try_acquire_lease,
    upsert_backfill_progress,
    upsert_company_info,
    upsert_entities,
    upsert_panel_cache,
    upsert_report_snapshot,
    upsert_sync_state,
)
from app.financial.quickbooks import QuickBooksError, _get, cdc_records, query_page, report

logger = logging.getLogger(__name__)

BACKFILL_START = "2024-01-01"
CDC_ENTITIES = [
    "Invoice", "Bill", "Payment", "Purchase", "PurchaseOrder",
    "BillPayment", "CreditMemo", "Customer", "Class", "Department",
]
TXN_ENTITIES = [e for e in CDC_ENTITIES if e not in ("Customer", "Class", "Department")]
_PAGE_SIZE = 1000


class LeaseHeld(RuntimeError):
    pass


def report_jobs(year: int, as_of: date) -> list[tuple[str, dict[str, str]]]:
    """Stable snapshot params per (report_name, year). Do not hash moving as_of."""
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    return [
        ("ProfitAndLoss", {
            "start_date": start,
            "end_date": end,
            "summarize_column_by": "Classes",
        }),
        ("ProfitAndLoss", {
            "start_date": start,
            "end_date": end,
            "summarize_column_by": "Departments",
        }),
        ("ProfitAndLoss", {
            "start_date": start,
            "end_date": end,
            "summarize_column_by": "Month",
        }),
        ("CustomerIncome", {"start_date": start, "end_date": end}),
        ("AgedReceivableDetail", {"report_date": end}),
        ("ExpensesByVendorSummary", {"start_date": start, "end_date": end}),
        ("SalesByCustomer", {"start_date": start, "end_date": end}),
        ("BalanceSheet", {"date": end}),
        ("CashFlow", {"start_date": start, "end_date": end}),
    ]


def _report_fetch_params(
    year: int,
    as_of: date,
    report_name: str,
    params: dict[str, str],
) -> dict[str, str]:
    """Historical years use that year's period end; current year may use today."""
    if year < as_of.year:
        return params
    as_of_s = as_of.isoformat()
    if report_name == "AgedReceivableDetail":
        return {**params, "report_date": as_of_s}
    if report_name == "BalanceSheet":
        return {**params, "date": as_of_s}
    if report_name == "CashFlow":
        return {**params, "end_date": as_of_s}
    return params


def _renew_sync_lease(realm_id: str, owner: str, *, stage: str) -> None:
    if not try_acquire_lease(realm_id, owner):
        logger.warning(
            "operation=renew_sync_lease realm_id=%s owner=%s stage=%s "
            "status=lease_held",
            realm_id,
            owner,
            stage,
        )
        raise LeaseHeld("QuickBooks sync lease renewal failed")


def _parse_cursor(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _backfill_years(started: datetime) -> list[int]:
    return sorted({2024, 2025, started.year})


def _activity_since(started: datetime) -> str:
    return started.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _is_report_permission_denied(exc: BaseException) -> bool:
    text = str(exc)
    return "5020" in text or "Permission Denied" in text


def _ingest_reports(realm_id: str, years: list[int], as_of: date, fetched_at: str) -> int:
    count = 0
    for year in years:
        for report_name, hash_params in report_jobs(year, as_of):
            fetch_params = _report_fetch_params(year, as_of, report_name, hash_params)
            try:
                logger.info(
                    "operation=ingest_report step=fetch realm_id=%s "
                    "report_name=%s year=%s",
                    realm_id,
                    report_name,
                    year,
                )
                payload = report(report_name, **fetch_params)
            except QuickBooksError as exc:
                if not _is_report_permission_denied(exc):
                    raise
                logger.warning(
                    "operation=ingest_report realm_id=%s report_name=%s year=%s "
                    "skipped=true reason=permission_denied",
                    realm_id,
                    report_name,
                    year,
                )
                continue
            upsert_report_snapshot({
                "realm_id": realm_id,
                "report_name": report_name,
                "year": year,
                "params": fetch_params,
                "params_hash": params_hash(hash_params),
                "payload": payload,
                "fetched_at": fetched_at,
            })
            count += 1
            logger.info(
                "operation=ingest_report step=stored realm_id=%s report_name=%s year=%s",
                realm_id,
                report_name,
                year,
            )
    return count


def _ingest_company_info(realm_id: str, fetched_at: str) -> None:
    payload = _get(f"companyinfo/{realm_id}")
    info = payload.get("CompanyInfo") or {}
    address = info.get("CompanyAddr") or {}
    sku = next(
        (item.get("Value") for item in info.get("NameValue", []) if item.get("Name") == "OfferingSku"),
        None,
    )
    upsert_company_info({
        "realm_id": realm_id,
        "company_name": info.get("CompanyName"),
        "legal_name": info.get("LegalName"),
        "city": address.get("City"),
        "state": address.get("CountrySubDivisionCode"),
        "fiscal_year_start": info.get("FiscalYearStartMonth"),
        "start_date": info.get("CompanyStartDate"),
        "sku": sku,
        "raw": info,
        "fetched_at": fetched_at,
    })
    logger.info("operation=ingest_company_info realm_id=%s", realm_id)


def _write_panel_cache(
    realm_id: str,
    years: list[int],
    as_of: date,
    activity_since: str,
    computed_at: str,
) -> None:
    payloads = [
        (
            year,
            build_overview(
                realm_id,
                year,
                as_of=as_of,
                activity_since=activity_since,
            ),
        )
        for year in years
    ]
    for year, payload in payloads:
        upsert_panel_cache(
            realm_id,
            year,
            payload,
            as_of.isoformat(),
            computed_at,
        )
        logger.info(
            "operation=write_panel_cache realm_id=%s year=%s",
            realm_id,
            year,
        )


def _backfill_entity(realm_id: str, entity: str, synced_at: str, owner: str) -> int:
    progress = get_backfill_progress(realm_id, entity) or {}
    if progress.get("completed"):
        logger.info(
            "operation=backfill_entity realm_id=%s entity=%s skipped=true reason=completed",
            realm_id,
            entity,
        )
        return 0

    position = int(progress.get("startposition") or 1)
    if entity in TXN_ENTITIES:
        sql = f"select * from {entity} where TxnDate >= '{BACKFILL_START}' ORDERBY Id"
    else:
        sql = f"select * from {entity} ORDERBY Id"

    total = 0
    while True:
        _renew_sync_lease(realm_id, owner, stage=entity)
        page = query_page(sql, entity, startposition=position)
        if page:
            upsert_entities(realm_id, entity, page, synced_at=synced_at)
            total += len(page)
        completed = len(page) < _PAGE_SIZE
        next_position = position + len(page)
        upsert_backfill_progress(realm_id, entity, next_position, completed)
        logger.info(
            "operation=backfill_page realm_id=%s entity=%s startposition=%s "
            "row_count=%s completed=%s",
            realm_id,
            entity,
            position,
            len(page),
            completed,
        )
        if completed:
            break
        position = next_position
    return total


def _run_backfill(*, realm_id: str, started: datetime, run_id: str, owner: str) -> dict[str, int]:
    synced_at = started.isoformat()
    counts: dict[str, int] = {}
    state = get_sync_state(realm_id) or {}
    if state.get("backfill_completed_at"):
        clear_backfill_progress(realm_id)
        logger.info(
            "operation=_run_backfill run_id=%s realm_id=%s reason=rerun_completed",
            run_id,
            realm_id,
        )
    upsert_sync_state(realm_id, {
        "last_started_at": synced_at,
        "last_mode": "backfill",
        **({"backfill_completed_at": None} if state.get("backfill_completed_at") else {}),
    })
    logger.info(
        "operation=_run_backfill run_id=%s realm_id=%s started=%s",
        run_id,
        realm_id,
        synced_at,
    )
    for entity in CDC_ENTITIES:
        counts[entity] = _backfill_entity(realm_id, entity, synced_at, owner)

    as_of = started.date()
    years = _backfill_years(started)
    counts["reports"] = _ingest_reports(realm_id, years, as_of, synced_at)
    _ingest_company_info(realm_id, synced_at)
    _write_panel_cache(
        realm_id,
        years,
        as_of,
        _activity_since(started),
        datetime.now(timezone.utc).isoformat(),
    )
    # No ANALYZE RPC exists in the migration. After the first backfill, run
    # ANALYZE on qb_* tables in the Supabase SQL editor so new indexes have stats.
    now = datetime.now(timezone.utc).isoformat()
    upsert_sync_state(realm_id, {
        "cdc_cursor": started.isoformat(),
        "backfill_completed_at": now,
        "last_success_at": now,
        "last_error": None,
        "last_mode": "backfill",
    })
    logger.info(
        "operation=_run_backfill run_id=%s realm_id=%s status=success entity_count=%s",
        run_id,
        realm_id,
        sum(v for k, v in counts.items() if k != "reports"),
    )
    return counts


def _run_nightly(
    *,
    realm_id: str,
    started: datetime,
    state: dict[str, Any],
    run_id: str,
    owner: str,
) -> dict[str, int]:
    cursor_raw = state.get("cdc_cursor")
    if not cursor_raw:
        raise RuntimeError("nightly sync requires cdc_cursor; run backfill first")
    cursor = _parse_cursor(cursor_raw)
    upsert_sync_state(realm_id, {
        "last_started_at": started.isoformat(),
        "last_mode": "nightly",
    })
    logger.info(
        "operation=_run_nightly step=cdc_fetch run_id=%s realm_id=%s cursor=%s "
        "entities=%s",
        run_id,
        realm_id,
        cursor.isoformat(),
        ",".join(CDC_ENTITIES),
    )
    _renew_sync_lease(realm_id, owner, stage="cdc")
    changed = cdc_records(CDC_ENTITIES, cursor.isoformat())
    synced_at = started.isoformat()
    counts: dict[str, int] = {}
    for entity in CDC_ENTITIES:
        payloads = changed.get(entity) or []
        logger.info(
            "operation=_run_nightly step=cdc_upsert run_id=%s entity=%s "
            "changed_count=%s",
            run_id,
            entity,
            len(payloads),
        )
        counts[entity] = upsert_entities(realm_id, entity, payloads, synced_at=synced_at)

    as_of = started.date()
    year = started.year
    logger.info(
        "operation=_run_nightly step=reports run_id=%s year=%s",
        run_id,
        year,
    )
    _renew_sync_lease(realm_id, owner, stage="reports")
    counts["reports"] = _ingest_reports(realm_id, [year], as_of, synced_at)
    logger.info("operation=_run_nightly step=company_info run_id=%s", run_id)
    _ingest_company_info(realm_id, synced_at)
    logger.info(
        "operation=_run_nightly step=panel_cache run_id=%s year=%s",
        run_id,
        year,
    )
    _write_panel_cache(
        realm_id,
        [year],
        as_of,
        _activity_since(started),
        datetime.now(timezone.utc).isoformat(),
    )
    now = datetime.now(timezone.utc).isoformat()
    logger.info(
        "operation=_run_nightly step=cursor_advanced run_id=%s cursor=%s",
        run_id,
        started.isoformat(),
    )
    upsert_sync_state(realm_id, {
        "cdc_cursor": started.isoformat(),
        "last_success_at": now,
        "last_error": None,
        "last_mode": "nightly",
    })
    logger.info(
        "operation=_run_nightly step=done run_id=%s realm_id=%s status=success",
        run_id,
        realm_id,
    )
    return counts


def _record_sync_failure(
    *,
    realm_id: str,
    run_id: str | None,
    mode: str,
    exc: Exception,
    duration_ms: int,
) -> None:
    logger.exception(
        "operation=run_sync run_id=%s mode=%s status=failed duration_ms=%s error_type=%s",
        run_id,
        mode,
        duration_ms,
        type(exc).__name__,
    )
    if run_id:
        try:
            finish_sync_run(run_id, "failed", error=str(exc)[:500])
        except Exception as cleanup_exc:
            logger.exception(
                "operation=finish_sync_run run_id=%s mode=%s status=failed "
                "error_type=%s",
                run_id,
                mode,
                type(cleanup_exc).__name__,
            )
    try:
        upsert_sync_state(realm_id, {"last_error": str(exc)[:500]})
    except Exception as cleanup_exc:
        logger.exception(
            "operation=upsert_sync_state run_id=%s realm_id=%s status=failed "
            "error_type=%s",
            run_id,
            realm_id,
            type(cleanup_exc).__name__,
        )


def run_sync(mode: str = "auto") -> dict[str, str]:
    realm = settings.quickbooks_realm_id
    owner = f"sync-{uuid4()}"
    t0 = time.monotonic()
    if not try_acquire_lease(realm, owner):
        logger.warning(
            "operation=run_sync realm_id=%s status=lease_held",
            realm,
        )
        raise LeaseHeld("QuickBooks sync lease is held")

    started = datetime.now(timezone.utc)
    run_id: str | None = None
    resolved_mode = mode
    try:
        state = get_sync_state(realm) or {}
        if mode == "auto":
            resolved_mode = "nightly" if state.get("backfill_completed_at") else "backfill"
        run_id = insert_sync_run({
            "realm_id": realm,
            "mode": resolved_mode,
            "status": "running",
            "started_at": started.isoformat(),
        })
        logger.info(
            "operation=run_sync step=start run_id=%s mode=%s status=running realm_id=%s",
            run_id,
            resolved_mode,
            realm,
        )
        if resolved_mode == "backfill":
            counts = _run_backfill(
                realm_id=realm,
                started=started,
                run_id=run_id,
                owner=owner,
            )
        elif resolved_mode == "nightly":
            counts = _run_nightly(
                realm_id=realm,
                started=started,
                state=state,
                run_id=run_id,
                owner=owner,
            )
        else:
            raise ValueError(f"Unknown sync mode: {mode}")
        finish_sync_run(run_id, "success", entities_upserted=counts)
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "operation=run_sync run_id=%s mode=%s status=success duration_ms=%s",
            run_id,
            resolved_mode,
            duration_ms,
        )
        return {"status": "success", "mode": resolved_mode, "run_id": run_id}
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _record_sync_failure(
            realm_id=realm,
            run_id=run_id,
            mode=resolved_mode,
            exc=exc,
            duration_ms=duration_ms,
        )
        raise
    finally:
        try:
            release_lease(realm, owner)
        except Exception as release_exc:
            logger.exception(
                "operation=release_lease run_id=%s realm_id=%s status=failed "
                "error_type=%s",
                run_id,
                realm,
                type(release_exc).__name__,
            )
