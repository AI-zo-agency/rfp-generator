"""Stable open-item IDs for Agency weekly carryover tracking."""

from __future__ import annotations

from typing import Any

PRIORITY = {"delivery": 0, "mapping": 1, "receivable": 2, "invoice": 3, "orphan": 4}


def _money(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0  # noqa: PLR0124 — NaN check


def _job_name(job: dict[str, Any]) -> str:
    return str(job.get("project_name") or job.get("job_label") or job.get("company_name") or "Untitled project")


def _receivable_key(job: dict[str, Any]) -> str:
    return str(job.get("client_map_id") or job.get("client_name") or job.get("company_name") or job.get("project_id") or "")


def _is_delivery_risk(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").lower()
    health = str(job.get("health") or "").lower()
    return status == "late" or health == "bad"


def _needs_mapping(job: dict[str, Any]) -> bool:
    return str(job.get("join") or "") in {"needs mapping", "ambiguous", "suggested"}


def _item(
    *,
    item_id: str,
    kind: str,
    title: str,
    detail: str,
    amount: float,
    go_to: str,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "amount": round(amount, 2),
        "go_to": go_to,
    }


def build_trackable_items(overview: dict[str, Any]) -> list[dict[str, Any]]:
    """Mirror frontend buildAgencyActions + orphan watchlist IDs."""
    items: list[dict[str, Any]] = []
    receivables: dict[str, dict[str, Any]] = {}
    jobs = overview.get("jobs") or []

    for job in jobs:
        if not isinstance(job, dict):
            continue
        amount = _money(job.get("open_ar"))
        name = _job_name(job)
        project_id = str(job.get("project_id") or "")

        if _is_delivery_risk(job):
            reason = "Late" if str(job.get("status") or "").lower() == "late" else "At-risk"
            items.append(
                _item(
                    item_id=f"delivery:{project_id}",
                    kind="delivery",
                    title=f"{reason}: {name}",
                    detail=str(job.get("company_name") or job.get("client_name") or job.get("job_label") or ""),
                    amount=amount,
                    go_to="jobs",
                )
            )

        if _needs_mapping(job):
            items.append(
                _item(
                    item_id=f"mapping:{project_id}",
                    kind="mapping",
                    title=f"Map: {name}",
                    detail=str(job.get("company_name") or job.get("client_name") or job.get("join") or ""),
                    amount=amount,
                    go_to="mapping",
                )
            )

        if amount > 0:
            key = _receivable_key(job)
            current = receivables.get(key)
            if current is None or _money(current.get("open_ar")) < amount:
                receivables[key] = job

    for job in receivables.values():
        amount = _money(job.get("open_ar"))
        key = _receivable_key(job)
        items.append(
            _item(
                item_id=f"receivable:{key}",
                kind="receivable",
                title=f"Collect: {job.get('client_name') or job.get('company_name') or _job_name(job)}",
                detail=_job_name(job),
                amount=amount,
                go_to="jobs",
            )
        )

    for invoice in overview.get("unlinked_invoices") or []:
        if not isinstance(invoice, dict):
            continue
        amount = _money(invoice.get("open_ar")) or _money(invoice.get("total_amt"))
        invoice_id = str(invoice.get("invoice_id") or "")
        label = str(invoice.get("invoice_number") or invoice_id)
        customer = str(invoice.get("customer_name") or invoice.get("customer_id") or "Unknown customer")
        items.append(
            _item(
                item_id=f"invoice:{invoice_id}",
                kind="invoice",
                title=f"Reconcile invoice {label}",
                detail=customer,
                amount=amount,
                go_to="invoices",
            )
        )

    for orphan in overview.get("billed_without_project") or []:
        if not isinstance(orphan, dict):
            continue
        customer_id = str(orphan.get("customer_id") or "")
        items.append(
            _item(
                item_id=f"orphan:{customer_id}",
                kind="orphan",
                title=str(orphan.get("customer_name") or customer_id),
                detail="Billed without a live Teamwork project",
                amount=_money(orphan.get("billed_ytd")),
                go_to="orphans",
            )
        )

    items.sort(
        key=lambda row: (
            PRIORITY.get(str(row.get("kind")), 99),
            -_money(row.get("amount")),
            str(row.get("title") or ""),
            str(row.get("id") or ""),
        )
    )
    return items


def extract_kpis(overview: dict[str, Any], open_items: list[dict[str, Any]]) -> dict[str, Any]:
    position = overview.get("position") if isinstance(overview.get("position"), dict) else {}
    orphans = overview.get("billed_without_project") or []
    orphan_billed = sum(_money(row.get("billed_ytd")) for row in orphans if isinstance(row, dict))
    queue_kinds = {"delivery", "mapping", "receivable"}
    return {
        "booked_ytd": _money(position.get("booked_ytd")),
        "open_ar": _money(position.get("open_ar")),
        "live_jobs": int(position.get("live_jobs") or 0),
        "overdue_tasks": int(position.get("overdue_tasks") or 0),
        "join_mapped": int(position.get("join_mapped") or 0),
        "join_total": int(position.get("join_total") or 0),
        "queue_count": sum(1 for row in open_items if row.get("kind") in queue_kinds),
        "unlinked_invoice_count": len(overview.get("unlinked_invoices") or []),
        "orphan_count": len(orphans),
        "orphan_billed_sum": round(orphan_billed, 2),
        "open_item_count": len(open_items),
    }
