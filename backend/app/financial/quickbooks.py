"""Read-only QuickBooks Online client and the dashboard panels built on it.

Every call in this module is a GET. There is deliberately no create/update/delete
path — the engagement mandates read-only access to the live company file.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from datetime import date, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services import quickbooks_oauth

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0
_PAGE = 1000


class QuickBooksError(RuntimeError):
    pass


# ── transport ────────────────────────────────────────────────────────────────

def _get(path: str, _retried: bool = False) -> dict[str, Any]:
    if not settings.quickbooks_configured:
        raise QuickBooksError("QuickBooks credentials are not configured")

    url = f"{settings.quickbooks_api_base}/v3/company/{settings.quickbooks_realm_id}/{path}"
    url += ("&" if "?" in url else "?") + f"minorversion={settings.quickbooks_minor_version}"

    for attempt in range(1, 5):
        response = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {quickbooks_oauth.get_access_token()}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT,
        )
        if response.status_code == 401 and not _retried:
            quickbooks_oauth.get_access_token(force=True)
            return _get(path, _retried=True)
        if response.status_code == 429 and attempt < 4:
            delay = 2 ** attempt
            logger.warning("operation=_get status=429 attempt=%s", attempt)
            time.sleep(delay)
            continue
        if response.status_code != 200:
            raise QuickBooksError(f"QuickBooks {response.status_code}: {response.text[:300]}")
        return response.json()
    raise QuickBooksError("QuickBooks 429: rate limited after retries")


def query_page(sql: str, key: str, startposition: int = 1) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(f"{sql} startposition {startposition} maxresults {_PAGE}")
    rows = _get(f"query?query={encoded}").get("QueryResponse", {}).get(key, []) or []
    logger.info(
        "[QB] operation=query_page key=%s startposition=%s rows=%s",
        key, startposition, len(rows),
    )
    return rows


def query(sql: str, key: str) -> list[dict[str, Any]]:
    """Run a QuickBooks query, following pagination to the end."""
    rows: list[dict[str, Any]] = []
    position = 1
    while True:
        page = query_page(sql, key, startposition=position)
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        position += _PAGE


def report(name: str, **params: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    return _get(f"reports/{name}" + (f"?{qs}" if qs else ""))


def cdc_records(entities: list[str], since: str) -> dict[str, list[dict[str, Any]]]:
    qs = urllib.parse.urlencode({
        "entities": ",".join(entities),
        "changedSince": since,
    })
    logger.info(
        "operation=cdc_records step=request since=%s entity_count=%s",
        since,
        len(entities),
    )
    payload = _get(f"cdc?{qs}")
    out: dict[str, list[dict[str, Any]]] = {e: [] for e in entities}
    for response in payload.get("CDCResponse", []):
        for query_response in response.get("QueryResponse", []):
            for entity, rows in query_response.items():
                if isinstance(rows, list):
                    out.setdefault(entity, []).extend(rows)
    logger.info("[QB] operation=cdc_records since=%s entities=%s", since, list(out.keys()))
    return out


def cdc(entities: list[str], since: str) -> dict[str, int]:
    return {k: len(v) for k, v in cdc_records(entities, since).items()}


# ── report helpers ───────────────────────────────────────────────────────────

def _flatten(node: Any, acc: list[list[str]] | None = None) -> list[list[str]]:
    """QuickBooks reports nest Rows/Row/Summary arbitrarily deep. Flatten to cells."""
    acc = acc if acc is not None else []
    rows = node.get("Row", []) if isinstance(node, dict) else node
    for row in rows if isinstance(rows, list) else [rows]:
        cells = row.get("ColData") or (row.get("Header") or {}).get("ColData")
        if cells:
            acc.append([c.get("value", "") for c in cells])
        if row.get("Rows"):
            _flatten(row["Rows"], acc)
        if row.get("Summary"):
            acc.append([c.get("value", "") for c in row["Summary"]["ColData"]])
    return acc


def _money(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _is_report_total_label(name: str) -> bool:
    label = name.strip().upper()
    return label == "TOTAL" or label.startswith("TOTAL ")


def _columns(payload: dict[str, Any]) -> list[str]:
    return [c.get("ColTitle", "") for c in payload.get("Columns", {}).get("Column", [])]


def _find_row(payload: dict[str, Any], label: str) -> list[str] | None:
    for row in _flatten(payload.get("Rows", {})):
        if row and row[0].strip().lower() == label.lower():
            return row
    return None


def _days_overdue(due: str, today: date) -> int:
    try:
        return (today - datetime.strptime(due, "%Y-%m-%d").date()).days
    except (ValueError, TypeError):
        return 0


def _bucket(days: int) -> str:
    if days <= 0:
        return "Not yet due"
    if days <= 30:
        return "1-30 days"
    if days <= 60:
        return "31-60 days"
    if days <= 90:
        return "61-90 days"
    return "90+ days"


AGING_BUCKETS = ["Not yet due", "1-30 days", "31-60 days", "61-90 days", "90+ days"]


# ── panels ───────────────────────────────────────────────────────────────────

def ar_aging(today: date | None = None) -> dict[str, Any]:
    """Panel A — money owed to zö, bucketed by age, with the worst debtors."""
    today = today or date.today()
    invoices = query("select * from Invoice where Balance > '0'", "Invoice")

    buckets = {b: {"amount": 0.0, "count": 0} for b in AGING_BUCKETS}
    by_client: dict[str, dict[str, Any]] = {}

    for invoice in invoices:
        balance = float(invoice.get("Balance", 0))
        due = invoice.get("DueDate") or invoice.get("TxnDate") or ""
        days = _days_overdue(due, today)
        slot = buckets[_bucket(days)]
        slot["amount"] += balance
        slot["count"] += 1

        name = (invoice.get("CustomerRef") or {}).get("name", "Unknown")
        entry = by_client.setdefault(name, {"client": name, "amount": 0.0, "invoices": 0, "oldest_days": 0})
        entry["amount"] += balance
        entry["invoices"] += 1
        entry["oldest_days"] = max(entry["oldest_days"], days)

    total = sum(b["amount"] for b in buckets.values())
    clients = sorted(by_client.values(), key=lambda c: -c["amount"])
    return {
        "total": round(total, 2),
        "invoice_count": len(invoices),
        "overdue_total": round(total - buckets["Not yet due"]["amount"], 2),
        "buckets": [
            {"label": b, "amount": round(v["amount"], 2), "count": v["count"],
             "pct": round(v["amount"] / total * 100, 1) if total else 0.0}
            for b, v in buckets.items()
        ],
        "clients": [{**c, "amount": round(c["amount"], 2)} for c in clients[:12]],
    }


def ap_aging(today: date | None = None) -> dict[str, Any]:
    """Panel B — money zö owes, by vendor."""
    today = today or date.today()
    bills = query("select * from Bill where Balance > '0'", "Bill")

    buckets = {b: 0.0 for b in AGING_BUCKETS}
    by_vendor: dict[str, float] = {}
    for bill in bills:
        balance = float(bill.get("Balance", 0))
        due = bill.get("DueDate") or bill.get("TxnDate") or ""
        buckets[_bucket(_days_overdue(due, today))] += balance
        name = (bill.get("VendorRef") or {}).get("name", "Unknown")
        by_vendor[name] = by_vendor.get(name, 0.0) + balance

    total = sum(buckets.values())
    vendors = sorted(by_vendor.items(), key=lambda kv: -kv[1])
    return {
        "total": round(total, 2),
        "bill_count": len(bills),
        "buckets": [{"label": b, "amount": round(v, 2)} for b, v in buckets.items()],
        "vendors": [{"vendor": n, "amount": round(v, 2)} for n, v in vendors[:10]],
    }


def revenue_by_class(year: int) -> dict[str, Any]:
    """Panel C — the Project/Recurring × Government/Private matrix.

    QuickBooks returns sub-classes as bare column titles, so `Government` and
    `Private Enterprise` each appear twice — once under Project Revenue and once
    under Recurring Revenue. Columns must be qualified by the parent that
    precedes them, and the `Total <parent>` subtotals skipped, or the matrix
    collapses into duplicate ambiguous cells.
    """
    payload = report(
        "ProfitAndLoss",
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        summarize_column_by="Classes",
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
            # `Total Project Revenue` closes a group — a subtotal, not a cell.
            parent = None
            continue
        if value == 0 and label not in segments:
            # A parent header carries no direct amount; children follow it.
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

    return {
        "matrix": matrix,
        "parents": parents,
        "segments": segments,
        "unclassified": round(unclassified, 2),
        "total": round(total, 2),
        "coverage_pct": round((total - unclassified) / total * 100, 1) if total else 0.0,
    }


def by_account_manager(year: int) -> dict[str, Any]:
    """Panel D — income and net by Department, which zö uses for account managers."""
    payload = report(
        "ProfitAndLoss",
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        summarize_column_by="Departments",
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
    return {"managers": managers}


def client_profitability(year: int) -> dict[str, Any]:
    """Panel E — CustomerIncome. Margin is overstated while cost attribution is thin."""
    payload = report("CustomerIncome", start_date=f"{year}-01-01", end_date=f"{year}-12-31")
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
    return {"clients": clients[:20], "attributed_expense": round(attributed_expense, 2)}


def monthly_trend(year: int) -> dict[str, Any]:
    """Panel F — booked revenue by month, straight from the ledger."""
    payload = report(
        "ProfitAndLoss",
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        summarize_column_by="Month",
    )
    columns = _columns(payload)
    income = _find_row(payload, "Total Income") or []
    months = []
    for column, value in zip(columns[1:], income[1:]):
        if column.strip().upper() == "TOTAL":
            continue
        months.append({"month": column.strip(), "amount": round(_money(value), 2)})
    booked = [m for m in months if m["amount"]]
    return {
        "months": months,
        "total": round(sum(m["amount"] for m in months), 2),
        "peak": round(max((m["amount"] for m in months), default=0.0), 2),
        "last_booked_month": booked[-1]["month"] if booked else None,
    }


# Accounts whose spend belongs to a client by definition. Anything here without a
# customer on the line is unattributed cost of delivery, not overhead.
_COST_OF_SERVICE_PREFIXES = ("COSS", "COL -", "COGS")


def unattached_cost(year: int) -> dict[str, Any]:
    """Panel G — cost-of-service spend carrying no client."""
    purchases = query(f"select * from Purchase where TxnDate >= '{year}-01-01'", "Purchase")

    total = 0.0
    unattached_count = 0
    accounts: dict[str, float] = {}

    for purchase in purchases:
        total += float(purchase.get("TotalAmt", 0))
        lines = purchase.get("Line", [])
        has_customer = any(
            (line.get("AccountBasedExpenseLineDetail") or {}).get("CustomerRef")
            or (line.get("ItemBasedExpenseLineDetail") or {}).get("CustomerRef")
            for line in lines
        )
        if has_customer:
            continue
        unattached_count += 1
        for line in lines:
            detail = line.get("AccountBasedExpenseLineDetail") or {}
            name = (detail.get("AccountRef") or {}).get("name", "(unaccounted)")
            accounts[name] = accounts.get(name, 0.0) + float(line.get("Amount", 0))

    cost_of_service = {
        name: amount
        for name, amount in accounts.items()
        if name.upper().startswith(_COST_OF_SERVICE_PREFIXES)
    }
    ranked = sorted(accounts.items(), key=lambda kv: -kv[1])

    return {
        "purchase_count": len(purchases),
        "purchase_total": round(total, 2),
        "unattached_count": unattached_count,
        "unattached_pct": round(unattached_count / len(purchases) * 100, 1) if purchases else 0.0,
        "cost_of_service_unattached": round(sum(cost_of_service.values()), 2),
        "accounts": [{"account": n, "amount": round(v, 2),
                      "is_cost_of_service": n.upper().startswith(_COST_OF_SERVICE_PREFIXES)}
                     for n, v in ranked[:12]],
    }


def recent_activity(since: str) -> dict[str, Any]:
    """Panel H — one CDC call covering every entity we care about."""
    counts = cdc(
        ["Invoice", "Bill", "Payment", "PurchaseOrder", "Customer", "Purchase"],
        since,
    )
    return {
        "since": since,
        "entities": [{"entity": k, "changed": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
        "total": sum(counts.values()),
    }


def company_profile() -> dict[str, Any]:
    realm = settings.quickbooks_realm_id
    info = _get(f"companyinfo/{realm}")["CompanyInfo"]
    address = info.get("CompanyAddr", {})
    return {
        "company_name": info.get("CompanyName"),
        "legal_name": info.get("LegalName"),
        "city": address.get("City"),
        "state": address.get("CountrySubDivisionCode"),
        "fiscal_year_start": info.get("FiscalYearStartMonth"),
        "start_date": info.get("CompanyStartDate"),
        "sku": next((p.get("Value") for p in info.get("NameValue", []) if p.get("Name") == "OfferingSku"), None),
    }


# ── Phase 1–4 insight panels (GET only) ──────────────────────────────────────

_MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _month_index(txn_date: str) -> int | None:
    try:
        return datetime.strptime(txn_date[:10], "%Y-%m-%d").month - 1
    except (ValueError, TypeError):
        return None


def _empty_month_series() -> list[dict[str, Any]]:
    return [{"month": label, "amount": 0.0} for label in _MONTH_LABELS]


def _timed(operation: str, **ctx: Any):
    """Context manager-ish timing via simple start/end logs."""
    import time as _time

    class _Timer:
        def __enter__(self):
            self.t0 = _time.monotonic()
            logger.info("[QB] %s start %s", operation, ctx)
            return self

        def __exit__(self, *exc):
            ms = int((_time.monotonic() - self.t0) * 1000)
            logger.info("[QB] %s done duration_ms=%s %s", operation, ms, ctx)

    return _Timer()


def cash_collections(year: int) -> dict[str, Any]:
    """YTD cash received from Payment entities."""
    with _timed("cash_collections", year=year):
        payments = query(f"select * from Payment where TxnDate >= '{year}-01-01'", "Payment")
        by_month = _empty_month_series()
        by_payer: dict[str, float] = {}
        total = 0.0
        for payment in payments:
            amt = float(payment.get("TotalAmt") or 0)
            total += amt
            idx = _month_index(payment.get("TxnDate") or "")
            if idx is not None:
                by_month[idx]["amount"] += amt
            name = (payment.get("CustomerRef") or {}).get("name", "Unknown")
            by_payer[name] = by_payer.get(name, 0.0) + amt
        for row in by_month:
            row["amount"] = round(row["amount"], 2)
        payers = sorted(by_payer.items(), key=lambda kv: -kv[1])
        logger.info("[QB] cash_collections rows=%s total=%s", len(payments), round(total, 2))
        return {
            "total_collected": round(total, 2),
            "payment_count": len(payments),
            "by_month": by_month,
            "top_payers": [{"customer": n, "amount": round(v, 2)} for n, v in payers[:10]],
        }


def billing_vs_cash(year: int) -> dict[str, Any]:
    """Invoiced vs collected by month for the year."""
    with _timed("billing_vs_cash", year=year):
        invoices = query(f"select * from Invoice where TxnDate >= '{year}-01-01'", "Invoice")
        payments = query(f"select * from Payment where TxnDate >= '{year}-01-01'", "Payment")

        invoiced_by = [0.0] * 12
        collected_by = [0.0] * 12
        invoiced_total = 0.0
        open_ar = 0.0
        for inv in invoices:
            amt = float(inv.get("TotalAmt") or 0)
            bal = float(inv.get("Balance") or 0)
            invoiced_total += amt
            open_ar += bal
            idx = _month_index(inv.get("TxnDate") or "")
            if idx is not None:
                invoiced_by[idx] += amt

        collected_total = 0.0
        for payment in payments:
            amt = float(payment.get("TotalAmt") or 0)
            collected_total += amt
            idx = _month_index(payment.get("TxnDate") or "")
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
            "[QB] billing_vs_cash invoices=%s payments=%s invoiced=%s collected=%s",
            len(invoices), len(payments), round(invoiced_total, 2), round(collected_total, 2),
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


def dso(year: int) -> dict[str, Any]:
    """Average days-to-pay from Payment→Invoice LinkedTxn links."""
    with _timed("dso", year=year):
        invoices = query(f"select * from Invoice where TxnDate >= '{year}-01-01'", "Invoice")
        payments = query(f"select * from Payment where TxnDate >= '{year}-01-01'", "Payment")
        by_id = {str(inv.get("Id")): inv for inv in invoices if inv.get("Id") is not None}

        samples: list[tuple[str, int, float]] = []
        for payment in payments:
            pay_date = payment.get("TxnDate") or ""
            customer = (payment.get("CustomerRef") or {}).get("name", "Unknown")
            for line in payment.get("Line") or []:
                for link in line.get("LinkedTxn") or []:
                    if (link.get("TxnType") or "").lower() != "invoice":
                        continue
                    inv = by_id.get(str(link.get("TxnId")))
                    if not inv:
                        continue
                    inv_date = inv.get("TxnDate") or ""
                    try:
                        days = (
                            datetime.strptime(pay_date[:10], "%Y-%m-%d").date()
                            - datetime.strptime(inv_date[:10], "%Y-%m-%d").date()
                        ).days
                    except (ValueError, TypeError):
                        continue
                    if days < 0:
                        continue
                    amt = float(line.get("Amount") or inv.get("TotalAmt") or 0)
                    samples.append((customer, days, amt))

        if not samples:
            logger.warning("[QB] dso no linked payment samples year=%s", year)
            return {"dso_days": None, "sample_size": 0, "slowest_clients": []}

        avg = sum(d for _, d, _ in samples) / len(samples)
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
        logger.info("[QB] dso sample_size=%s dso_days=%s", len(samples), round(avg, 1))
        return {
            "dso_days": round(avg, 1),
            "sample_size": len(samples),
            "slowest_clients": slowest[:8],
        }


def aged_ar_detail(today: date | None = None) -> dict[str, Any]:
    """Intuit AgedReceivableDetail — flattened buckets when parseable."""
    today = today or date.today()
    with _timed("aged_ar_detail"):
        payload = report("AgedReceivableDetail", report_date=today.isoformat())
        # Prefer summary-style totals from column headers when present.
        columns = _columns(payload)
        rows = _flatten(payload.get("Rows", {}))
        # Fall back: return raw column titles + row count for UI to prefer custom ar panel.
        logger.info("[QB] aged_ar_detail columns=%s rows=%s", len(columns), len(rows))
        return {
            "report_date": today.isoformat(),
            "columns": columns,
            "row_count": len(rows),
            "source": "AgedReceivableDetail",
        }


def purchase_orders(year: int) -> dict[str, Any]:
    """Open and YTD purchase orders (commitments)."""
    with _timed("purchase_orders", year=year):
        pos = query("select * from PurchaseOrder", "PurchaseOrder")
        open_total = 0.0
        ytd_total = 0.0
        by_vendor: dict[str, float] = {}
        open_count = 0
        ytd_count = 0
        for po in pos:
            amt = float(po.get("TotalAmt") or 0)
            txn = po.get("TxnDate") or ""
            in_year = txn.startswith(str(year))
            status = (po.get("POStatus") or "").lower()
            vendor = (po.get("VendorRef") or {}).get("name", "Unknown")
            if in_year:
                ytd_total += amt
                ytd_count += 1
            if status in ("", "open"):
                open_total += amt
                open_count += 1
                by_vendor[vendor] = by_vendor.get(vendor, 0.0) + amt
        vendors = sorted(by_vendor.items(), key=lambda kv: -kv[1])
        logger.info("[QB] purchase_orders total=%s ytd=%s open=%s", len(pos), ytd_count, open_count)
        return {
            "po_count": ytd_count,
            "open_count": open_count,
            "open_total": round(open_total, 2),
            "ytd_total": round(ytd_total, 2),
            "vendors": [{"vendor": n, "amount": round(v, 2)} for n, v in vendors[:10]],
        }


def expenses_by_vendor(year: int) -> dict[str, Any]:
    """Vendor spend concentration from VendorExpenses."""
    with _timed("expenses_by_vendor", year=year):
        payload = report(
            "VendorExpenses",
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
        )
        vendors: list[dict[str, Any]] = []
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
            vendors.append({"vendor": name, "amount": round(amount, 2)})
        if not total:
            total = sum(v["amount"] for v in vendors)
        vendors.sort(key=lambda v: -v["amount"])
        top = vendors[:12]
        top_sum = sum(v["amount"] for v in top[:3])
        concentration_pct = round(top_sum / total * 100, 1) if total else 0.0
        logger.info("[QB] expenses_by_vendor vendors=%s total=%s", len(vendors), round(total, 2))
        return {
            "total": round(total, 2),
            "vendor_count": len(vendors),
            "top3_concentration_pct": concentration_pct,
            "vendors": top,
        }


def bill_payments(year: int) -> dict[str, Any]:
    """Cash out via BillPayment."""
    with _timed("bill_payments", year=year):
        rows = query(f"select * from BillPayment where TxnDate >= '{year}-01-01'", "BillPayment")
        by_month = _empty_month_series()
        total = 0.0
        for bp in rows:
            amt = float(bp.get("TotalAmt") or 0)
            total += amt
            idx = _month_index(bp.get("TxnDate") or "")
            if idx is not None:
                by_month[idx]["amount"] += amt
        for row in by_month:
            row["amount"] = round(row["amount"], 2)
        logger.info("[QB] bill_payments count=%s total=%s", len(rows), round(total, 2))
        return {
            "total_paid": round(total, 2),
            "payment_count": len(rows),
            "by_month": by_month,
        }


def customers_directory() -> dict[str, Any]:
    """Active customer list — join keys for future Teamwork mapping."""
    with _timed("customers_directory"):
        rows = query("select * from Customer where Active = true", "Customer")
        customers = [
            {
                "id": str(c.get("Id")),
                "display_name": c.get("DisplayName") or c.get("FullyQualifiedName") or "",
                "company_name": c.get("CompanyName") or "",
                "balance": round(float(c.get("Balance") or 0), 2),
            }
            for c in rows
        ]
        customers.sort(key=lambda c: c["display_name"].lower())
        logger.info("[QB] customers_directory count=%s", len(customers))
        return {"count": len(customers), "customers": customers[:200]}


def sales_by_customer(year: int) -> dict[str, Any]:
    """Ranked customer revenue from CustomerSales."""
    with _timed("sales_by_customer", year=year):
        payload = report(
            "CustomerSales",
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
        )
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
        logger.info("[QB] sales_by_customer clients=%s", len(clients))
        return {"total": round(total, 2), "clients": clients[:25]}


def credit_memos(year: int) -> dict[str, Any]:
    """Credits / write-downs for the year."""
    with _timed("credit_memos", year=year):
        rows = query(f"select * from CreditMemo where TxnDate >= '{year}-01-01'", "CreditMemo")
        total = 0.0
        by_client: dict[str, float] = {}
        for cm in rows:
            amt = float(cm.get("TotalAmt") or 0)
            total += amt
            name = (cm.get("CustomerRef") or {}).get("name", "Unknown")
            by_client[name] = by_client.get(name, 0.0) + amt
        ranked = sorted(by_client.items(), key=lambda kv: -kv[1])
        logger.info("[QB] credit_memos count=%s total=%s", len(rows), round(total, 2))
        return {
            "total": round(total, 2),
            "count": len(rows),
            "clients": [{"client": n, "amount": round(v, 2)} for n, v in ranked[:10]],
        }


def class_coverage(year: int) -> dict[str, Any]:
    """Class list for coding coverage (P&L coverage_pct comes from revenue_by_class panel)."""
    with _timed("class_coverage", year=year):
        classes = query("select * from Class", "Class")
        names = [c.get("Name") for c in classes if c.get("Name")]
        return {
            "class_count": len(names),
            "classes": names[:40],
            # Filled lightly here; UI prefers revenue_by_class.coverage_pct when present.
            "coverage_pct": 0.0,
            "unclassified": 0.0,
            "total": 0.0,
        }


def department_coverage(year: int) -> dict[str, Any]:
    """Department list for AM coding coverage (income split from by_account_manager)."""
    with _timed("department_coverage", year=year):
        deps = query("select * from Department", "Department")
        return {
            "department_count": len(deps),
            "departments": [d.get("Name") for d in deps if d.get("Name")][:40],
            "overhead_income": 0.0,
            "overhead_pct": 0.0,
            "manager_count": len(deps),
        }


def liquidity(year: int, today: date | None = None) -> dict[str, Any]:
    """Balance sheet cash + cash-flow operating signal."""
    today = today or date.today()
    with _timed("liquidity", year=year):
        bs = report("BalanceSheet", date=today.isoformat())
        cf = report(
            "CashFlow",
            start_date=f"{year}-01-01",
            end_date=today.isoformat(),
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
        # Fallback: sum rows that look like checking/savings under assets.
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

        logger.info("[QB] liquidity cash=%s net_cash=%s", cash, net_cash)
        return {
            "as_of": today.isoformat(),
            "cash": round(cash, 2),
            "net_cash_change": round(net_cash, 2) if net_cash is not None else None,
        }
