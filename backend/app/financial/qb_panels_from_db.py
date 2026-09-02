"""Dashboard panels computed from the QuickBooks mirror, never from Intuit."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable

from app.financial import qb_repository as repo
from app.financial.qb_cost_completeness import cost_completeness
from app.financial.qb_map import params_hash
from app.financial.quickbooks import (
    AGING_BUCKETS,
    _COST_OF_SERVICE_PREFIXES,
    _MONTH_LABELS,
    _bucket,
    _columns,
    _days_overdue,
    _empty_month_series,
    _find_row,
    _flatten,
    _is_report_total_label,
    _money,
    _month_index,
)

logger = logging.getLogger(__name__)

_ACTIVITY_ENTITIES = (
    ("Invoice", "list_invoices"),
    ("Bill", "list_bills"),
    ("Payment", "list_payments"),
    ("PurchaseOrder", "list_purchase_orders"),
    ("Customer", "list_customers"),
    ("Purchase", "list_purchases"),
)


def _date_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _amount(row: dict[str, Any], key: str = "total_amt") -> float:
    return float(row.get(key) or 0)


def _year_start(year: int) -> str:
    return f"{year}-01-01"


def _year_end(year: int) -> str:
    return f"{year}-12-31"


# ── repository wrappers (patched by tests) ───────────────────────────────────

def list_open_invoices(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_invoices(
        realm_id, is_deleted=False, balance__gt=0, **filters,
    )


def list_open_bills(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_bills(
        realm_id, is_deleted=False, balance__gt=0, **filters,
    )


def list_invoices(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_invoices(realm_id, **filters)


def list_bills(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_bills(realm_id, **filters)


def list_payments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_payments(realm_id, **filters)


def list_purchases(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_purchases(realm_id, **filters)


def list_purchase_lines(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_purchase_lines(realm_id, **filters)


def list_txn_links(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    return repo.list_txn_links(realm_id, **filters)


def list_purchase_orders(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_purchase_orders(realm_id, **filters)


def list_bill_payments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_bill_payments(realm_id, **filters)


def list_credit_memos(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_credit_memos(realm_id, **filters)


def list_customers(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_customers(realm_id, **filters)


def list_classes(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_classes(realm_id, **filters)


def list_departments(realm_id: str, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("is_deleted", False)
    return repo.list_departments(realm_id, **filters)


def get_report_snapshot(
    realm_id: str,
    report_name: str,
    year: int,
    params_hash: str,
) -> dict[str, Any] | None:
    return repo.get_report_snapshot(realm_id, report_name, year, params_hash)


def get_company_info(realm_id: str) -> dict[str, Any] | None:
    return repo.get_company_info(realm_id)


def count_activity(realm_id: str, since: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entity, lister_name in _ACTIVITY_ENTITIES:
        lister = globals()[lister_name]
        rows = lister(realm_id, qbo_updated_at__gte=since)
        counts[entity] = len(rows)
    total = sum(counts.values())
    logger.info(
        "operation=count_activity realm_id=%s since=%s total=%s",
        realm_id,
        since,
        total,
    )
    return {
        "since": since,
        "entities": [
            {"entity": name, "changed": count}
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
        "total": total,
    }


def _snapshot_payload(
    realm_id: str,
    report_name: str,
    year: int,
    params: dict[str, str],
) -> dict[str, Any]:
    hashed = params_hash(params)
    snap = get_report_snapshot(realm_id, report_name, year, hashed)
    if not snap:
        logger.warning(
            "operation=get_report_snapshot realm_id=%s report_name=%s "
            "year=%s missing=true",
            realm_id,
            report_name,
            year,
        )
        raise LookupError(f"missing {report_name} snapshot for year {year}")
    payload = snap.get("payload")
    if not isinstance(payload, dict):
        raise LookupError(f"{report_name} snapshot payload missing for year {year}")
    return payload


def _first_snapshot_payload(
    realm_id: str,
    year: int,
    params: dict[str, str],
    *report_names: str,
) -> dict[str, Any]:
    last_error: LookupError | None = None
    for report_name in report_names:
        try:
            return _snapshot_payload(realm_id, report_name, year, params)
        except LookupError as exc:
            last_error = exc
    raise last_error or LookupError(f"missing snapshot for year {year}")


# ── entity panels ────────────────────────────────────────────────────────────

def ar_aging(realm_id: str, *, as_of: date) -> dict[str, Any]:
    invoices = list_open_invoices(realm_id)
    buckets = {b: {"amount": 0.0, "count": 0} for b in AGING_BUCKETS}
    by_client: dict[str, dict[str, Any]] = {}

    for invoice in invoices:
        if invoice.get("is_deleted"):
            continue
        balance = _amount(invoice, "balance")
        due = _date_str(invoice.get("due_date") or invoice.get("txn_date"))
        days = _days_overdue(due, as_of)
        slot = buckets[_bucket(days)]
        slot["amount"] += balance
        slot["count"] += 1

        name = invoice.get("customer_name") or "Unknown"
        entry = by_client.setdefault(
            name,
            {
                "client": name, "amount": 0.0, "invoices": 0, "oldest_days": 0,
                "overdue_amount": 0.0, "overdue_days": 0,
                "overdue_dollar_days": 0.0,
            },
        )
        entry["amount"] += balance
        entry["invoices"] += 1
        entry["oldest_days"] = max(entry["oldest_days"], days)
        # `amount` sums every open invoice while `oldest_days` keeps only the
        # worst age, so the pair can describe invoices with nothing in common.
        # Splitting off the overdue portion fixes half of that; the other half
        # is that one 73-day invoice makes a client's whole overdue balance look
        # 73 days late. Only the exact per-invoice sum settles it, and this loop
        # is already standing on every invoice with its own age in hand.
        if days > 0:
            entry["overdue_amount"] += balance
            entry["overdue_days"] = max(entry["overdue_days"], days)
            entry["overdue_dollar_days"] += balance * days

    total = sum(b["amount"] for b in buckets.values())
    clients = sorted(by_client.values(), key=lambda c: -c["amount"])
    logger.info(
        "operation=ar_aging realm_id=%s as_of=%s invoice_count=%s total=%s",
        realm_id,
        as_of.isoformat(),
        len(invoices),
        round(total, 2),
    )
    return {
        "total": round(total, 2),
        "invoice_count": len(invoices),
        "overdue_total": round(total - buckets["Not yet due"]["amount"], 2),
        "buckets": [
            {
                "label": label,
                "amount": round(values["amount"], 2),
                "count": values["count"],
                "pct": round(values["amount"] / total * 100, 1) if total else 0.0,
            }
            for label, values in buckets.items()
        ],
        "clients": [
            {
                **c,
                "amount": round(c["amount"], 2),
                "overdue_amount": round(c["overdue_amount"], 2),
                "overdue_dollar_days": round(c["overdue_dollar_days"], 2),
            }
            for c in clients[:12]
        ],
    }


def ap_aging(realm_id: str, *, as_of: date) -> dict[str, Any]:
    bills = list_open_bills(realm_id)
    buckets = {b: 0.0 for b in AGING_BUCKETS}
    by_vendor: dict[str, float] = {}
    for bill in bills:
        if bill.get("is_deleted"):
            continue
        balance = _amount(bill, "balance")
        due = _date_str(bill.get("due_date") or bill.get("txn_date"))
        buckets[_bucket(_days_overdue(due, as_of))] += balance
        name = bill.get("vendor_name") or "Unknown"
        by_vendor[name] = by_vendor.get(name, 0.0) + balance

    total = sum(buckets.values())
    vendors = sorted(by_vendor.items(), key=lambda kv: -kv[1])
    logger.info(
        "operation=ap_aging realm_id=%s as_of=%s bill_count=%s total=%s",
        realm_id,
        as_of.isoformat(),
        len(bills),
        round(total, 2),
    )
    return {
        "total": round(total, 2),
        "bill_count": len(bills),
        "buckets": [{"label": label, "amount": round(amount, 2)} for label, amount in buckets.items()],
        "vendors": [{"vendor": name, "amount": round(amount, 2)} for name, amount in vendors[:10]],
    }


def unattached_cost(realm_id: str, year: int) -> dict[str, Any]:
    purchases = list_purchases(realm_id, txn_date__gte=_year_start(year))
    lines = list_purchase_lines(realm_id)
    lines_by_purchase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        purchase_id = line.get("purchase_id")
        if purchase_id is not None:
            lines_by_purchase[str(purchase_id)].append(line)

    total = 0.0
    unattached_count = 0
    accounts: dict[str, float] = {}

    for purchase in purchases:
        total += _amount(purchase)
        pid = str(purchase.get("qbo_id"))
        purchase_lines = lines_by_purchase.get(pid, [])
        if any(line.get("customer_id") for line in purchase_lines):
            continue
        unattached_count += 1
        for line in purchase_lines:
            name = line.get("account_name") or "(unaccounted)"
            accounts[name] = accounts.get(name, 0.0) + _amount(line, "amount")

    cost_of_service = {
        name: amount
        for name, amount in accounts.items()
        if name.upper().startswith(_COST_OF_SERVICE_PREFIXES)
    }
    ranked = sorted(accounts.items(), key=lambda kv: -kv[1])
    logger.info(
        "operation=unattached_cost realm_id=%s year=%s purchase_count=%s "
        "unattached_count=%s",
        realm_id,
        year,
        len(purchases),
        unattached_count,
    )
    return {
        "purchase_count": len(purchases),
        "purchase_total": round(total, 2),
        "unattached_count": unattached_count,
        "unattached_pct": round(unattached_count / len(purchases) * 100, 1) if purchases else 0.0,
        "cost_of_service_unattached": round(sum(cost_of_service.values()), 2),
        "accounts": [
            {
                "account": name,
                "amount": round(amount, 2),
                "is_cost_of_service": name.upper().startswith(_COST_OF_SERVICE_PREFIXES),
            }
            for name, amount in ranked[:12]
        ],
    }


def cash_collections(realm_id: str, year: int) -> dict[str, Any]:
    payments = list_payments(realm_id, txn_date__gte=_year_start(year))
    by_month = _empty_month_series()
    by_payer: dict[str, float] = {}
    total = 0.0
    for payment in payments:
        amt = _amount(payment)
        total += amt
        idx = _month_index(_date_str(payment.get("txn_date")))
        if idx is not None:
            by_month[idx]["amount"] += amt
        name = payment.get("customer_name") or "Unknown"
        by_payer[name] = by_payer.get(name, 0.0) + amt
    for row in by_month:
        row["amount"] = round(row["amount"], 2)
    payers = sorted(by_payer.items(), key=lambda kv: -kv[1])
    logger.info(
        "operation=cash_collections realm_id=%s year=%s payment_count=%s total=%s",
        realm_id,
        year,
        len(payments),
        round(total, 2),
    )
    return {
        "total_collected": round(total, 2),
        "payment_count": len(payments),
        "by_month": by_month,
        "top_payers": [{"customer": name, "amount": round(amount, 2)} for name, amount in payers[:10]],
    }


def billing_vs_cash(realm_id: str, year: int) -> dict[str, Any]:
    invoices = list_invoices(realm_id, txn_date__gte=_year_start(year))
    payments = list_payments(realm_id, txn_date__gte=_year_start(year))

    invoiced_by = [0.0] * 12
    collected_by = [0.0] * 12
    invoiced_total = 0.0
    open_ar = 0.0
    for invoice in invoices:
        amt = _amount(invoice)
        invoiced_total += amt
        open_ar += _amount(invoice, "balance")
        idx = _month_index(_date_str(invoice.get("txn_date")))
        if idx is not None:
            invoiced_by[idx] += amt

    collected_total = 0.0
    for payment in payments:
        amt = _amount(payment)
        collected_total += amt
        idx = _month_index(_date_str(payment.get("txn_date")))
        if idx is not None:
            collected_by[idx] += amt

    by_month = [
        {
            "month": _MONTH_LABELS[i],
            "invoiced": round(invoiced_by[i], 2),
            "collected": round(collected_by[i], 2),
        }
        for i in range(12)
    ]
    rate = round(collected_total / invoiced_total * 100, 1) if invoiced_total else 0.0
    logger.info(
        "operation=billing_vs_cash realm_id=%s year=%s invoice_count=%s "
        "payment_count=%s invoiced=%s collected=%s",
        realm_id,
        year,
        len(invoices),
        len(payments),
        round(invoiced_total, 2),
        round(collected_total, 2),
    )
    return {
        "invoiced_total": round(invoiced_total, 2),
        "collected_total": round(collected_total, 2),
        "open_ar": round(open_ar, 2),
        "collection_rate_pct": rate,
        "invoice_count": len(invoices),
        "payment_count": len(payments),
        "by_month": by_month,
    }


def dso(realm_id: str, year: int) -> dict[str, Any]:
    invoices = list_invoices(realm_id, txn_date__gte=_year_start(year))
    payments = list_payments(realm_id, txn_date__gte=_year_start(year))
    links = list_txn_links(realm_id, to_type="Invoice")
    by_id = {str(inv.get("qbo_id")): inv for inv in invoices if inv.get("qbo_id") is not None}
    payments_by_id = {
        str(payment.get("qbo_id")): payment
        for payment in payments
        if payment.get("qbo_id") is not None
    }

    samples: list[tuple[str, int, float]] = []
    for link in links:
        if (link.get("to_type") or "").lower() != "invoice":
            continue
        payment = payments_by_id.get(str(link.get("from_id")))
        invoice = by_id.get(str(link.get("to_id")))
        if not payment or not invoice:
            continue
        pay_date = _date_str(payment.get("txn_date"))
        inv_date = _date_str(invoice.get("txn_date"))
        try:
            days = (
                datetime.strptime(pay_date, "%Y-%m-%d").date()
                - datetime.strptime(inv_date, "%Y-%m-%d").date()
            ).days
        except (ValueError, TypeError):
            continue
        if days < 0:
            continue
        amt = float(link.get("amount") or invoice.get("total_amt") or 0)
        customer = payment.get("customer_name") or "Unknown"
        samples.append((customer, days, amt))

    if not samples:
        logger.warning(
            "operation=dso realm_id=%s year=%s sample_size=0",
            realm_id,
            year,
        )
        return {"dso_days": None, "sample_size": 0, "slowest_clients": []}

    avg = sum(days for _, days, _ in samples) / len(samples)
    by_client: dict[str, list[tuple[int, float]]] = {}
    for customer, days, amt in samples:
        by_client.setdefault(customer, []).append((days, amt))
    slowest = []
    for customer, rows in by_client.items():
        avg_days = sum(d for d, _ in rows) / len(rows)
        amount = sum(a for _, a in rows)
        slowest.append({
            "client": customer,
            "avg_days": round(avg_days, 1),
            "amount": round(amount, 2),
        })
    slowest.sort(key=lambda c: -c["avg_days"])
    logger.info(
        "operation=dso realm_id=%s year=%s sample_size=%s dso_days=%s",
        realm_id,
        year,
        len(samples),
        round(avg, 1),
    )
    return {
        "dso_days": round(avg, 1),
        "sample_size": len(samples),
        "slowest_clients": slowest[:8],
    }


def purchase_orders(realm_id: str, year: int) -> dict[str, Any]:
    pos = list_purchase_orders(realm_id)
    open_total = 0.0
    ytd_total = 0.0
    by_vendor: dict[str, float] = {}
    open_count = 0
    ytd_count = 0
    year_prefix = str(year)
    for po in pos:
        amt = _amount(po)
        txn = _date_str(po.get("txn_date"))
        in_year = txn.startswith(year_prefix)
        status = (po.get("po_status") or "").lower()
        vendor = po.get("vendor_name") or "Unknown"
        if in_year:
            ytd_total += amt
            ytd_count += 1
        if status in ("", "open"):
            open_total += amt
            open_count += 1
            by_vendor[vendor] = by_vendor.get(vendor, 0.0) + amt
    vendors = sorted(by_vendor.items(), key=lambda kv: -kv[1])
    logger.info(
        "operation=purchase_orders realm_id=%s year=%s po_count=%s open_count=%s",
        realm_id,
        year,
        ytd_count,
        open_count,
    )
    return {
        "po_count": ytd_count,
        "open_count": open_count,
        "open_total": round(open_total, 2),
        "ytd_total": round(ytd_total, 2),
        "vendors": [{"vendor": name, "amount": round(amount, 2)} for name, amount in vendors[:10]],
    }


def bill_payments(realm_id: str, year: int) -> dict[str, Any]:
    rows = list_bill_payments(realm_id, txn_date__gte=_year_start(year))
    by_month = _empty_month_series()
    total = 0.0
    for row in rows:
        amt = _amount(row)
        total += amt
        idx = _month_index(_date_str(row.get("txn_date")))
        if idx is not None:
            by_month[idx]["amount"] += amt
    for row in by_month:
        row["amount"] = round(row["amount"], 2)
    logger.info(
        "operation=bill_payments realm_id=%s year=%s payment_count=%s total=%s",
        realm_id,
        year,
        len(rows),
        round(total, 2),
    )
    return {
        "total_paid": round(total, 2),
        "payment_count": len(rows),
        "by_month": by_month,
    }


def customers_directory(realm_id: str) -> dict[str, Any]:
    rows = list_customers(realm_id, active=True)
    customers = []
    for row in rows:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        customers.append({
            "id": str(row.get("qbo_id")),
            "display_name": row.get("display_name") or raw.get("FullyQualifiedName") or "",
            "company_name": raw.get("CompanyName") or "",
            "balance": round(_amount(row, "balance"), 2),
        })
    customers.sort(key=lambda c: c["display_name"].lower())
    logger.info(
        "operation=customers_directory realm_id=%s count=%s",
        realm_id,
        len(customers),
    )
    return {"count": len(customers), "customers": customers[:200]}


def credit_memos(realm_id: str, year: int) -> dict[str, Any]:
    rows = list_credit_memos(realm_id, txn_date__gte=_year_start(year))
    total = 0.0
    by_client: dict[str, float] = {}
    for row in rows:
        amt = _amount(row)
        total += amt
        name = row.get("customer_name") or "Unknown"
        by_client[name] = by_client.get(name, 0.0) + amt
    ranked = sorted(by_client.items(), key=lambda kv: -kv[1])
    logger.info(
        "operation=credit_memos realm_id=%s year=%s count=%s total=%s",
        realm_id,
        year,
        len(rows),
        round(total, 2),
    )
    return {
        "total": round(total, 2),
        "count": len(rows),
        "clients": [{"client": name, "amount": round(amount, 2)} for name, amount in ranked[:10]],
    }


def class_coverage(realm_id: str, year: int) -> dict[str, Any]:
    classes = list_classes(realm_id)
    names = [row.get("name") for row in classes if row.get("name")]
    logger.info(
        "operation=class_coverage realm_id=%s year=%s class_count=%s",
        realm_id,
        year,
        len(names),
    )
    return {
        "class_count": len(names),
        "classes": names[:40],
        "coverage_pct": 0.0,
        "unclassified": 0.0,
        "total": 0.0,
    }


def department_coverage(realm_id: str, year: int) -> dict[str, Any]:
    deps = list_departments(realm_id)
    names = [row.get("name") for row in deps if row.get("name")]
    logger.info(
        "operation=department_coverage realm_id=%s year=%s department_count=%s",
        realm_id,
        year,
        len(names),
    )
    return {
        "department_count": len(names),
        "departments": names[:40],
        "overhead_income": 0.0,
        "overhead_pct": 0.0,
        "manager_count": len(deps),
    }


def company_profile(realm_id: str) -> dict[str, Any]:
    row = get_company_info(realm_id)
    if not row:
        logger.warning("operation=company_profile realm_id=%s missing=true", realm_id)
        raise LookupError("missing company info")
    logger.info("operation=company_profile realm_id=%s", realm_id)
    return {
        "company_name": row.get("company_name"),
        "legal_name": row.get("legal_name"),
        "city": row.get("city"),
        "state": row.get("state"),
        "fiscal_year_start": row.get("fiscal_year_start"),
        "start_date": row.get("start_date"),
        "sku": row.get("sku"),
    }


# ── report-backed panels ─────────────────────────────────────────────────────

def revenue_by_class(realm_id: str, year: int) -> dict[str, Any]:
    payload = _snapshot_payload(
        realm_id,
        "ProfitAndLoss",
        year,
        {
            "start_date": _year_start(year),
            "end_date": _year_end(year),
            "summarize_column_by": "Classes",
        },
    )
    columns = _columns(payload)
    income = _find_row(payload, "Total Income") or []
    values = [_money(v) for v in income[1:]] if income else []

    matrix: list[dict[str, Any]] = []
    parents: list[str] = []
    segments: list[str] = []
    parent: str | None = None
    unclassified = 0.0
    total = 0.0

    for column, value in zip(columns[1:], values):
        label = column.strip()
        upper = label.upper()

        if upper == "TOTAL":
            total = value
            continue
        if label.lower() == "not specified":
            unclassified = value
            continue
        if upper.startswith("TOTAL "):
            parent = None
            continue
        if value == 0 and label not in segments:
            parent = label
            if label not in parents:
                parents.append(label)
            continue

        matrix.append({
            "parent": parent or "Unassigned",
            "segment": label,
            "amount": round(value, 2),
        })
        if label not in segments:
            segments.append(label)

    logger.info(
        "operation=revenue_by_class realm_id=%s year=%s total=%s",
        realm_id,
        year,
        round(total, 2),
    )
    return {
        "matrix": matrix,
        "parents": parents,
        "segments": segments,
        "unclassified": round(unclassified, 2),
        "total": round(total, 2),
        "coverage_pct": round((total - unclassified) / total * 100, 1) if total else 0.0,
    }


def by_account_manager(realm_id: str, year: int) -> dict[str, Any]:
    payload = _snapshot_payload(
        realm_id,
        "ProfitAndLoss",
        year,
        {
            "start_date": _year_start(year),
            "end_date": _year_end(year),
            "summarize_column_by": "Departments",
        },
    )
    columns = _columns(payload)
    income = _find_row(payload, "Total Income") or []
    net = _find_row(payload, "Net Income") or []

    managers = []
    for index, column in enumerate(columns[1:], start=1):
        label = column.strip()
        if label.upper() == "TOTAL":
            continue
        revenue = _money(income[index]) if index < len(income) else 0.0
        profit = _money(net[index]) if index < len(net) else 0.0
        if not revenue and not profit:
            continue
        managers.append({
            "manager": label,
            "income": round(revenue, 2),
            "net": round(profit, 2),
            "is_overhead": label.lower() == "not specified",
        })
    managers.sort(key=lambda m: -m["income"])
    logger.info(
        "operation=by_account_manager realm_id=%s year=%s manager_count=%s",
        realm_id,
        year,
        len(managers),
    )
    return {"managers": managers}


def client_profitability(realm_id: str, year: int) -> dict[str, Any]:
    payload = _snapshot_payload(
        realm_id,
        "CustomerIncome",
        year,
        {"start_date": _year_start(year), "end_date": _year_end(year)},
    )
    clients = []
    attributed_expense = 0.0
    for row in _flatten(payload.get("Rows", {})):
        if len(row) < 4 or not row[0] or row[0].strip().upper() == "TOTAL":
            continue
        income, expense, net = _money(row[1]), _money(row[2]), _money(row[3])
        if not income and not expense:
            continue
        attributed_expense += abs(expense)
        clients.append({
            "client": row[0],
            "income": round(income, 2),
            "expense": round(abs(expense), 2),
            "net": round(net, 2),
            "margin_pct": round(net / income * 100, 1) if income else None,
        })
    clients.sort(key=lambda c: -c["income"])
    logger.info(
        "operation=client_profitability realm_id=%s year=%s client_count=%s",
        realm_id,
        year,
        len(clients),
    )
    return {"clients": clients[:20], "attributed_expense": round(attributed_expense, 2)}


def monthly_trend(realm_id: str, year: int) -> dict[str, Any]:
    payload = _snapshot_payload(
        realm_id,
        "ProfitAndLoss",
        year,
        {
            "start_date": _year_start(year),
            "end_date": _year_end(year),
            "summarize_column_by": "Month",
        },
    )
    columns = _columns(payload)
    income = _find_row(payload, "Total Income") or []
    # The same Month-summarized snapshot carries these, and pl_summary already
    # proves both rows resolve. Carrying them per month is what lets a
    # year-over-year margin comparison use the same months on both sides
    # instead of eight months against twelve.
    gross = _find_row(payload, "Gross Profit") or []
    cost = _find_row(payload, "Total Cost of Goods Sold") or []

    def _cell(row: list[Any], index: int) -> float | None:
        return round(_money(row[index]), 2) if index < len(row) else None

    months = []
    for index, (column, value) in enumerate(zip(columns[1:], income[1:]), start=1):
        if column.strip().upper() == "TOTAL":
            continue
        entry: dict[str, Any] = {
            "month": column.strip(), "amount": round(_money(value), 2),
        }
        # Absent rather than zero when the snapshot has no such row, so a
        # consumer can tell "no margin data" from "margin was nil".
        gross_profit = _cell(gross, index)
        if gross_profit is not None:
            entry["gross_profit"] = gross_profit
        cost_of_services = _cell(cost, index)
        if cost_of_services is not None:
            entry["cost_of_services"] = cost_of_services
        months.append(entry)
    booked = [m for m in months if m["amount"]]
    logger.info(
        "operation=monthly_trend realm_id=%s year=%s month_count=%s",
        realm_id,
        year,
        len(months),
    )
    return {
        "months": months,
        "total": round(sum(m["amount"] for m in months), 2),
        "peak": round(max((m["amount"] for m in months), default=0.0), 2),
        "last_booked_month": booked[-1]["month"] if booked else None,
    }


def _row_total(payload: dict[str, Any], label: str) -> float | None:
    row = _find_row(payload, label)
    if not row:
        return None
    return round(_money(row[-1]), 2)


def pl_summary(realm_id: str, year: int) -> dict[str, Any]:
    """Year P&L headlines from the Month snapshot Total column — not a P&L page."""
    payload = _snapshot_payload(
        realm_id,
        "ProfitAndLoss",
        year,
        {
            "start_date": _year_start(year),
            "end_date": _year_end(year),
            "summarize_column_by": "Month",
        },
    )
    income = _row_total(payload, "Total Income")
    cost_of_services = _row_total(payload, "Total Cost of Goods Sold")
    gross_profit = _row_total(payload, "Gross Profit")
    if gross_profit is None and income is not None and cost_of_services is not None:
        gross_profit = round(income - cost_of_services, 2)
    gross_margin_pct = (
        round(gross_profit / income * 100, 1)
        if gross_profit is not None and income
        else None
    )
    net_income = _row_total(payload, "Net Income")
    logger.info(
        "operation=pl_summary realm_id=%s year=%s income=%s gross_profit=%s net_income=%s",
        realm_id,
        year,
        income,
        gross_profit,
        net_income,
    )
    return {
        "income": income,
        "cost_of_services": cost_of_services,
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct,
        "net_income": net_income,
    }


def aged_ar_detail(realm_id: str, year: int, *, as_of: date) -> dict[str, Any]:
    payload = _snapshot_payload(
        realm_id,
        "AgedReceivableDetail",
        year,
        {"report_date": _year_end(year)},
    )
    columns = _columns(payload)
    rows = _flatten(payload.get("Rows", {}))
    logger.info(
        "operation=aged_ar_detail realm_id=%s year=%s as_of=%s row_count=%s",
        realm_id,
        year,
        as_of.isoformat(),
        len(rows),
    )
    return {
        "report_date": as_of.isoformat(),
        "columns": columns,
        "row_count": len(rows),
        "source": "AgedReceivableDetail",
    }


def _vendor_concentration(by_vendor: dict[str, float]) -> dict[str, Any]:
    ranked = sorted(by_vendor.items(), key=lambda kv: -kv[1])
    total = sum(amount for _, amount in ranked)
    top = ranked[:12]
    top_sum = sum(amount for _, amount in top[:3])
    return {
        "total": round(total, 2),
        "vendor_count": len(ranked),
        "top3_concentration_pct": round(top_sum / total * 100, 1) if total else 0.0,
        "vendors": [
            {"vendor": name, "amount": round(amount, 2)} for name, amount in top
        ],
    }


def _expenses_from_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    vendors: dict[str, float] = {}
    total = 0.0
    for row in _flatten(payload.get("Rows", {})):
        if len(row) < 2 or not row[0]:
            continue
        amount = 0.0
        for cell in reversed(row[1:]):
            parsed = _money(cell)
            if parsed or str(cell).strip() in ("0", "0.00"):
                amount = parsed
                break
        name = row[0].strip()
        if _is_report_total_label(name):
            if amount:
                total = amount
            continue
        vendors[name] = vendors.get(name, 0.0) + amount
    result = _vendor_concentration(vendors)
    if total:
        result["total"] = round(total, 2)
    return result


def _expenses_from_entities(realm_id: str, year: int) -> dict[str, Any]:
    start = _year_start(year)
    by_vendor: dict[str, float] = {}
    for row in list_bills(realm_id, txn_date__gte=start):
        name = row.get("vendor_name") or "Unknown"
        by_vendor[name] = by_vendor.get(name, 0.0) + _amount(row)
    for row in list_purchases(realm_id, txn_date__gte=start):
        name = row.get("vendor_name") or "Unknown"
        by_vendor[name] = by_vendor.get(name, 0.0) + _amount(row)
    result = _vendor_concentration(by_vendor)
    logger.info(
        "operation=expenses_by_vendor source=entities realm_id=%s year=%s "
        "vendor_count=%s total=%s",
        realm_id,
        year,
        result["vendor_count"],
        result["total"],
    )
    return result


def expenses_by_vendor(realm_id: str, year: int) -> dict[str, Any]:
    try:
        payload = _first_snapshot_payload(
            realm_id,
            year,
            {"start_date": _year_start(year), "end_date": _year_end(year)},
            "VendorExpenses",
            "ExpensesByVendorSummary",
        )
    except LookupError:
        logger.warning(
            "operation=expenses_by_vendor realm_id=%s year=%s "
            "status=fallback source=entities reason=missing_snapshot",
            realm_id,
            year,
        )
        return _expenses_from_entities(realm_id, year)
    result = _expenses_from_snapshot(payload)
    logger.info(
        "operation=expenses_by_vendor source=snapshot realm_id=%s year=%s "
        "vendor_count=%s total=%s",
        realm_id,
        year,
        result["vendor_count"],
        result["total"],
    )
    return result


def _sales_from_sales_report(payload: dict[str, Any]) -> dict[str, Any]:
    clients: list[dict[str, Any]] = []
    total = 0.0
    for row in _flatten(payload.get("Rows", {})):
        if len(row) < 2 or not row[0]:
            continue
        label = row[0].strip()
        if label.upper() in ("TOTAL", "TOTAL INCOME", ""):
            if label.upper().startswith("TOTAL"):
                total = _money(row[-1]) or total
            continue
        amount = _money(row[-1])
        if not amount:
            continue
        clients.append({"client": label, "amount": round(amount, 2)})
        total += amount
    clients.sort(key=lambda c: -c["amount"])
    return {"total": round(total, 2), "clients": clients[:25]}


def _sales_from_customer_income(payload: dict[str, Any]) -> dict[str, Any]:
    clients: list[dict[str, Any]] = []
    total = 0.0
    for row in _flatten(payload.get("Rows", {})):
        if len(row) < 2 or not row[0]:
            continue
        label = row[0].strip()
        if label.upper() in ("TOTAL", "TOTAL INCOME", ""):
            continue
        amount = _money(row[1]) if len(row) > 1 else 0.0
        if not amount:
            continue
        clients.append({"client": label, "amount": round(amount, 2)})
        total += amount
    clients.sort(key=lambda c: -c["amount"])
    return {"total": round(total, 2), "clients": clients[:25]}


def _sales_from_invoices(realm_id: str, year: int) -> dict[str, Any]:
    by_client: dict[str, float] = {}
    for invoice in list_invoices(realm_id, txn_date__gte=_year_start(year)):
        name = invoice.get("customer_name") or "Unknown"
        by_client[name] = by_client.get(name, 0.0) + _amount(invoice)
    ranked = sorted(by_client.items(), key=lambda kv: -kv[1])
    total = sum(amount for _, amount in ranked)
    logger.info(
        "operation=sales_by_customer source=invoices realm_id=%s year=%s "
        "client_count=%s total=%s",
        realm_id,
        year,
        len(ranked),
        round(total, 2),
    )
    return {
        "total": round(total, 2),
        "clients": [
            {"client": name, "amount": round(amount, 2)} for name, amount in ranked[:25]
        ],
    }


def sales_by_customer(realm_id: str, year: int) -> dict[str, Any]:
    try:
        payload = _first_snapshot_payload(
            realm_id,
            year,
            {"start_date": _year_start(year), "end_date": _year_end(year)},
            "CustomerSales",
            "SalesByCustomer",
        )
    except LookupError:
        try:
            payload = _snapshot_payload(
                realm_id,
                "CustomerIncome",
                year,
                {"start_date": _year_start(year), "end_date": _year_end(year)},
            )
        except LookupError:
            logger.warning(
                "operation=sales_by_customer realm_id=%s year=%s "
                "status=fallback source=invoices reason=missing_snapshot",
                realm_id,
                year,
            )
            return _sales_from_invoices(realm_id, year)
        result = _sales_from_customer_income(payload)
        logger.warning(
            "operation=sales_by_customer realm_id=%s year=%s "
            "status=fallback source=CustomerIncome client_count=%s",
            realm_id,
            year,
            len(result["clients"]),
        )
        return result
    result = _sales_from_sales_report(payload)
    logger.info(
        "operation=sales_by_customer source=snapshot realm_id=%s year=%s "
        "client_count=%s",
        realm_id,
        year,
        len(result["clients"]),
    )
    return result


def liquidity(realm_id: str, year: int, *, as_of: date) -> dict[str, Any]:
    bs = _snapshot_payload(
        realm_id,
        "BalanceSheet",
        year,
        {"date": _year_end(year)},
    )
    cf = _snapshot_payload(
        realm_id,
        "CashFlow",
        year,
        {"start_date": _year_start(year), "end_date": _year_end(year)},
    )
    cash = 0.0
    for row in _flatten(bs.get("Rows", {})):
        if not row:
            continue
        label = row[0].strip().lower()
        if label in ("total bank accounts", "cash and cash equivalents", "total cash"):
            cash = _money(row[-1])
            break
        if "bank accounts" in label and label.startswith("total"):
            cash = _money(row[-1])
            break
    if not cash:
        for row in _flatten(bs.get("Rows", {})):
            if row and "checking" in row[0].lower():
                cash += _money(row[-1])

    net_cash = None
    for row in _flatten(cf.get("Rows", {})):
        if not row:
            continue
        label = row[0].strip().lower()
        if "net cash increase" in label or label == "net cash":
            net_cash = _money(row[-1])
            break
        if "net cash provided by operating" in label:
            net_cash = _money(row[-1])

    logger.info(
        "operation=liquidity realm_id=%s year=%s as_of=%s cash=%s",
        realm_id,
        year,
        as_of.isoformat(),
        round(cash, 2),
    )
    return {
        "as_of": as_of.isoformat(),
        "cash": round(cash, 2),
        "net_cash_change": round(net_cash, 2) if net_cash is not None else None,
    }


# ── overview ─────────────────────────────────────────────────────────────────

def build_overview(
    realm_id: str,
    year: int,
    *,
    as_of: date,
    activity_since: str,
) -> dict[str, Any]:
    logger.info(
        "operation=build_overview realm_id=%s year=%s as_of=%s",
        realm_id,
        year,
        as_of.isoformat(),
    )
    panels: dict[str, Any] = {"year": year, "errors": {}}
    jobs: dict[str, Callable[[], Any]] = {
        "company": lambda: company_profile(realm_id),
        "ar": lambda: ar_aging(realm_id, as_of=as_of),
        "ap": lambda: ap_aging(realm_id, as_of=as_of),
        "revenue_by_class": lambda: revenue_by_class(realm_id, year),
        "by_account_manager": lambda: by_account_manager(realm_id, year),
        "client_profitability": lambda: client_profitability(realm_id, year),
        "monthly_trend": lambda: monthly_trend(realm_id, year),
        "pl_summary": lambda: pl_summary(realm_id, year),
        # Reads panels["monthly_trend"], so it must stay after it: jobs run in
        # insertion order and a failed trend leaves this one to degrade on None.
        "cost_completeness": lambda: cost_completeness(
            realm_id, year, as_of=as_of, monthly_trend=panels.get("monthly_trend")
        ),
        "unattached_cost": lambda: unattached_cost(realm_id, year),
        "activity": lambda: count_activity(realm_id, activity_since),
        "cash_collections": lambda: cash_collections(realm_id, year),
        "billing_vs_cash": lambda: billing_vs_cash(realm_id, year),
        "dso": lambda: dso(realm_id, year),
        "aged_ar_detail": lambda: aged_ar_detail(realm_id, year, as_of=as_of),
        "purchase_orders": lambda: purchase_orders(realm_id, year),
        "expenses_by_vendor": lambda: expenses_by_vendor(realm_id, year),
        "bill_payments": lambda: bill_payments(realm_id, year),
        "customers": lambda: customers_directory(realm_id),
        "sales_by_customer": lambda: sales_by_customer(realm_id, year),
        "credit_memos": lambda: credit_memos(realm_id, year),
        "class_coverage": lambda: class_coverage(realm_id, year),
        "department_coverage": lambda: department_coverage(realm_id, year),
        "liquidity": lambda: liquidity(realm_id, year, as_of=as_of),
    }
    for name, job in jobs.items():
        try:
            panels[name] = job()
        except LookupError as exc:
            logger.warning(
                "operation=build_overview realm_id=%s year=%s panel=%s "
                "status=skipped reason=missing_snapshot",
                realm_id,
                year,
                name,
            )
            panels[name] = None
            panels["errors"][name] = str(exc)[:200]
        except Exception as exc:  # noqa: BLE001 — one bad panel shouldn't blank the page
            logger.warning(
                "operation=build_overview realm_id=%s year=%s panel=%s failed",
                realm_id,
                year,
                name,
                exc_info=True,
            )
            panels[name] = None
            panels["errors"][name] = str(exc)[:200]
    panels["generated_at"] = datetime.now().isoformat()
    logger.info(
        "operation=build_overview realm_id=%s year=%s error_count=%s",
        realm_id,
        year,
        len(panels["errors"]),
    )
    return panels
