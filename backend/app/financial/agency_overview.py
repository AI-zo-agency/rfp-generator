"""Agency Jobs overview: Teamwork projects joined to confirmed QuickBooks money."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.config import settings
from app.financial import client_map_repository as map_repo
from app.financial.client_map import Ambiguous, ClientMatch, parse_job_key, resolve_project
from app.financial.qb_panels_from_db import _amount, _year_start, list_open_invoices, list_invoices, pl_summary
from app.financial.teamwork.teamwork_sync import _site_id, overview_from_cache

logger = logging.getLogger(__name__)


def money_by_customer_id(
    realm_id: str,
    year: int,
    *,
    invoices: list[dict[str, Any]] | None = None,
    open_invoices: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """YTD billed + open AR keyed by QuickBooks customer id.

    Name-only sales snapshots are not used — Agency money must join on ids.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for invoice in invoices if invoices is not None else list_invoices(
        realm_id, txn_date__gte=_year_start(year)
    ):
        if invoice.get("is_deleted"):
            continue
        cid = invoice.get("customer_id")
        if cid is None:
            continue
        key = str(cid)
        entry = by_id.setdefault(
            key,
            {
                "customer_id": key,
                "customer_name": invoice.get("customer_name") or "",
                "billed_ytd": 0.0,
                "open_ar": 0.0,
            },
        )
        entry["billed_ytd"] += _amount(invoice)
        if invoice.get("customer_name") and not entry["customer_name"]:
            entry["customer_name"] = invoice.get("customer_name")

    for invoice in open_invoices if open_invoices is not None else list_open_invoices(realm_id):
        if invoice.get("is_deleted"):
            continue
        cid = invoice.get("customer_id")
        if cid is None:
            continue
        key = str(cid)
        entry = by_id.setdefault(
            key,
            {
                "customer_id": key,
                "customer_name": invoice.get("customer_name") or "",
                "billed_ytd": 0.0,
                "open_ar": 0.0,
            },
        )
        entry["open_ar"] += _amount(invoice, "balance")
        if invoice.get("customer_name") and not entry["customer_name"]:
            entry["customer_name"] = invoice.get("customer_name")

    for entry in by_id.values():
        entry["billed_ytd"] = round(entry["billed_ytd"], 2)
        entry["open_ar"] = round(entry["open_ar"], 2)
    return by_id


def _sum_money(ids: list[str] | None, money: dict[str, dict[str, Any]]) -> tuple[float | None, float | None]:
    if not ids:
        return None, None
    billed = 0.0
    ar = 0.0
    hit = False
    for raw in ids:
        entry = money.get(str(raw))
        if entry is None:
            continue
        hit = True
        billed += float(entry.get("billed_ytd") or 0)
        ar += float(entry.get("open_ar") or 0)
    if not hit:
        return 0.0, 0.0
    return round(billed, 2), round(ar, 2)


def _join_label(match: ClientMatch | Ambiguous | None) -> str:
    if match is None:
        return "needs mapping"
    if isinstance(match, Ambiguous):
        return "ambiguous"
    if match.is_internal:
        return "internal"
    if match.via == "override" and match.link_confidence == "confirmed":
        return "job override"
    if match.link_confidence == "confirmed":
        return "confirmed"
    if match.link_confidence == "suggested":
        return "suggested"
    return "needs mapping"


def _money_ids(match: ClientMatch | Ambiguous | None) -> list[str] | None:
    """Only confirmed (or override-confirmed) ids drive dollars."""
    if not isinstance(match, ClientMatch):
        return None
    if match.is_internal:
        return None
    if match.link_confidence != "confirmed":
        return None
    return list(match.qb_customer_ids or [])


def build_job_row(
    project: dict[str, Any],
    *,
    match: ClientMatch | Ambiguous | None,
    hours_mtd_minutes: int,
    money: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = parse_job_key(str(project.get("name") or ""))
    job_label = (
        f"{key['tag']} {key['job_number']}"
        if key
        else str(project.get("name") or "")[:24]
    )
    client_name = None
    if isinstance(match, ClientMatch):
        client_name = match.client_name
    billed, ar = _sum_money(_money_ids(match), money)
    return {
        "project_id": str(project.get("id") or ""),
        "job_label": job_label,
        "project_name": project.get("name") or "",
        "company_name": project.get("company_name") or "",
        "client_name": client_name or project.get("company_name") or "—",
        "status": project.get("status") or "",
        "health": project.get("health") or "unset",
        "hours_mtd_minutes": int(hours_mtd_minutes or 0),
        "billed_ytd": billed,
        "open_ar": ar,
        "join": _join_label(match),
        "client_map_id": match.client_map_id if isinstance(match, ClientMatch) else None,
        "link_confidence": (
            match.link_confidence if isinstance(match, ClientMatch) else None
        ),
        "via": match.via if isinstance(match, ClientMatch) else None,
    }


def billed_without_live_project(
    money: dict[str, dict[str, Any]],
    *,
    linked_customer_ids: set[str],
    min_billed: float = 1.0,
) -> list[dict[str, Any]]:
    """QB customers with YTD income and no confirmed link on a live job."""
    rows = [
        {
            "customer_id": entry["customer_id"],
            "customer_name": entry["customer_name"] or entry["customer_id"],
            "billed_ytd": entry["billed_ytd"],
            "open_ar": entry["open_ar"],
        }
        for cid, entry in money.items()
        if cid not in linked_customer_ids and float(entry.get("billed_ytd") or 0) >= min_billed
    ]
    rows.sort(key=lambda row: -float(row["billed_ytd"]))
    return rows


def unlinked_invoices(
    invoices: list[dict[str, Any]], *, resolutions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Invoices still requiring a project or internal resolution."""
    resolved_invoice_ids = {
        str(resolution["invoice_id"])
        for resolution in resolutions
        if resolution.get("resolution") in {"linked", "internal"}
        and resolution.get("invoice_id") is not None
    }
    rows = []
    for invoice in invoices:
        if (
            invoice.get("is_deleted")
            or invoice.get("qbo_id") is None
            or not str(invoice["qbo_id"]).strip()
        ):
            continue
        invoice_id = str(invoice["qbo_id"])
        if invoice_id in resolved_invoice_ids:
            continue
        open_ar = _amount(invoice, "balance")
        supplied_status = invoice.get("status")
        status = supplied_status if isinstance(supplied_status, str) and supplied_status else (
            "open" if open_ar > 0 else "paid"
        )
        rows.append(
            {
                "invoice_id": invoice_id,
                "invoice_number": invoice.get("doc_number"),
                "customer_id": invoice.get("customer_id"),
                "customer_name": invoice.get("customer_name"),
                "txn_date": invoice.get("txn_date"),
                "due_date": invoice.get("due_date"),
                "total_amt": _amount(invoice),
                "open_ar": open_ar,
                "status": status,
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["open_ar"]),
            -float(row["total_amt"]),
            str(row["invoice_id"]),
        )
    )
    return rows


def build_agency_overview(*, year: int | None = None) -> dict[str, Any]:
    year = year if year is not None else date.today().year
    site_id = _site_id()
    realm_id = settings.quickbooks_realm_id
    cached_overview = overview_from_cache()
    tw = cached_overview if isinstance(cached_overview, dict) else {}
    projects = [project for project in (tw.get("projects") or []) if isinstance(project, dict)]
    clients = map_repo.list_client_map()
    overrides: dict[int, dict[str, Any]] = {}
    for override in map_repo.list_job_overrides(site_id=site_id):
        if not isinstance(override, dict) or override.get("project_id") is None:
            continue
        try:
            overrides[int(override["project_id"])] = override
        except (TypeError, ValueError):
            continue
    invoices = list_invoices(realm_id, txn_date__gte=_year_start(year))
    money = money_by_customer_id(realm_id, year, invoices=invoices)
    invoice_resolutions = map_repo.list_invoice_resolutions(realm_id)
    time_data = tw.get("time")
    time_by_project = time_data.get("by_project") if isinstance(time_data, dict) else []
    minutes_by_project: dict[str, int] = {}
    for bucket in time_by_project or []:
        if not isinstance(bucket, dict) or bucket.get("id") is None:
            continue
        try:
            minutes_by_project[str(bucket["id"])] = int(bucket.get("minutes") or 0)
        except (TypeError, ValueError):
            continue

    jobs: list[dict[str, Any]] = []
    linked_customer_ids: set[str] = set()
    for project in projects:
        pid = project.get("id")
        try:
            project_id_int = int(pid) if pid is not None and str(pid).isdigit() else int(pid or 0)
        except (TypeError, ValueError):
            project_id_int = 0
        match = resolve_project(
            site_id,
            project_id_int,
            str(project.get("name") or ""),
            project.get("company_id"),
            project.get("company_name"),
            client_rows=clients,
            overrides_loaded=True,
            override=overrides.get(project_id_int),
        )
        if isinstance(match, ClientMatch) and match.client_map_id and not match.client_name:
            mapped = next((c for c in clients if c.get("id") == match.client_map_id), None)
            if mapped:
                match = ClientMatch(
                    client_map_id=match.client_map_id,
                    tag_code=mapped.get("tag_code") or match.tag_code,
                    client_name=mapped.get("client_name"),
                    qb_customer_ids=match.qb_customer_ids
                    or list(mapped.get("qb_customer_ids") or []),
                    link_confidence=match.link_confidence,
                    via=match.via,
                    is_internal=bool(mapped.get("is_internal")) or match.is_internal,
                )
        row = build_job_row(
            project,
            match=match,
            hours_mtd_minutes=minutes_by_project.get(str(pid), 0),
            money=money,
        )
        jobs.append(row)
        for cid in _money_ids(match) or []:
            linked_customer_ids.add(str(cid))

    jobs.sort(key=lambda row: str(row["job_label"]))
    needs_mapping = [
        {
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "company_name": row["company_name"],
            "join": row["join"],
        }
        for row in jobs
        if row["join"] in {"needs mapping", "ambiguous", "suggested"}
    ]
    orphan_billed = billed_without_live_project(money, linked_customer_ids=linked_customer_ids)
    invoice_exceptions = unlinked_invoices(invoices, resolutions=invoice_resolutions)

    booked = 0.0
    try:
        booked = float((pl_summary(realm_id, year) or {}).get("income") or 0)
    except Exception:  # noqa: BLE001
        logger.exception("operation=agency_overview pl_summary_failed year=%s", year)
        booked = sum(float(m.get("billed_ytd") or 0) for m in money.values())

    open_ar_total = round(sum(float(m.get("open_ar") or 0) for m in money.values()), 2)
    money_ready = sum(
        1 for row in jobs if row["join"] in {"confirmed", "job override", "internal"}
    )
    cached_summary = tw.get("summary")
    summary = cached_summary if isinstance(cached_summary, dict) else {}

    payload = {
        "year": year,
        "as_of": tw.get("as_of") or tw.get("generated_at"),
        "position": {
            "booked_ytd": round(booked, 2),
            "open_ar": open_ar_total,
            "live_jobs": int(summary.get("project_count") or len(jobs)),
            "overdue_tasks": int(summary.get("overdue_task_count") or 0),
            "join_mapped": money_ready,
            "join_total": len(jobs),
        },
        "jobs": jobs,
        "needs_mapping": needs_mapping,
        "billed_without_project": orphan_billed[:40],
        "unlinked_invoices": invoice_exceptions[:40],
        "resolution_options": [
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "company_name": row["company_name"],
                "client_map_id": row["client_map_id"],
            }
            for row in jobs
        ],
    }
    logger.info(
        "operation=agency_overview year=%s jobs=%s mapped=%s needs_mapping=%s orphans=%s invoice_exceptions=%s",
        year,
        len(jobs),
        money_ready,
        len(needs_mapping),
        len(orphan_billed),
        len(invoice_exceptions),
    )
    return payload
