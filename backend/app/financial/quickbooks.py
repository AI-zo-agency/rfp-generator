"""Read-only QuickBooks Online client and the dashboard panels built on it.

Every call in this module is a GET. There is deliberately no create/update/delete
path — the engagement mandates read-only access to the live company file.
"""

from __future__ import annotations

import logging
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
    if response.status_code != 200:
        raise QuickBooksError(f"QuickBooks {response.status_code}: {response.text[:300]}")
    return response.json()


def query(sql: str, key: str) -> list[dict[str, Any]]:
    """Run a QuickBooks query, following pagination to the end."""
    rows: list[dict[str, Any]] = []
    position = 1
    while True:
        encoded = urllib.parse.quote(f"{sql} startposition {position} maxresults {_PAGE}")
        page = _get(f"query?query={encoded}")["QueryResponse"].get(key, [])
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        position += _PAGE


def report(name: str, **params: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    return _get(f"reports/{name}" + (f"?{qs}" if qs else ""))


def cdc(entities: list[str], since: str) -> dict[str, int]:
    payload = _get(f"cdc?entities={','.join(entities)}&changedSince={since}")
    counts: dict[str, int] = {}
    for response in payload.get("CDCResponse", []):
        for query_response in response.get("QueryResponse", []):
            for entity, rows in query_response.items():
                if isinstance(rows, list):
                    counts[entity] = len(rows)
    return counts


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
