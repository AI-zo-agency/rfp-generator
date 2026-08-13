"""Map QuickBooks Online entity JSON to Supabase row dicts."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ENTITY_TABLES = {
    "Invoice": "qb_invoices",
    "Bill": "qb_bills",
    "Payment": "qb_payments",
    "Purchase": "qb_purchases",
    "PurchaseOrder": "qb_purchase_orders",
    "BillPayment": "qb_bill_payments",
    "CreditMemo": "qb_credit_memos",
    "Customer": "qb_customers",
    "Class": "qb_classes",
    "Department": "qb_departments",
}

_LIST_ENTITIES = frozenset({"Customer", "Class", "Department"})


def _ref_id(payload: dict, key: str) -> str | None:
    ref = payload.get(key) or {}
    return str(ref["value"]) if ref.get("value") is not None else None


def _ref_name(payload: dict, key: str) -> str | None:
    ref = payload.get(key) or {}
    return ref.get("name")


def is_qbo_deleted(payload: dict) -> bool:
    return (payload.get("status") or "").lower() == "deleted"


def params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode()).hexdigest()


def _base_row(
    realm_id: str,
    entity: str,
    payload: dict,
    *,
    synced_at: str,
) -> dict[str, Any]:
    qbo_id = str(payload["Id"]) if payload.get("Id") is not None else None
    metadata = payload.get("MetaData") or {}
    row: dict[str, Any] = {
        "realm_id": realm_id,
        "qbo_id": qbo_id,
        "sync_token": payload.get("SyncToken"),
        "is_deleted": is_qbo_deleted(payload),
        "txn_date": None if entity in _LIST_ENTITIES else payload.get("TxnDate"),
        "qbo_updated_at": metadata.get("LastUpdatedTime"),
        "synced_at": synced_at,
        "raw": payload,
    }
    logger.debug(
        "entity_row entity=%s qbo_id=%s realm_id=%s",
        entity,
        qbo_id,
        realm_id,
    )
    return row


def entity_row(
    realm_id: str,
    entity: str,
    payload: dict,
    *,
    synced_at: str,
) -> dict[str, Any]:
    row = _base_row(realm_id, entity, payload, synced_at=synced_at)

    if entity == "Invoice":
        row.update({
            "doc_number": payload.get("DocNumber"),
            "due_date": payload.get("DueDate"),
            "total_amt": payload.get("TotalAmt"),
            "balance": payload.get("Balance"),
            "customer_id": _ref_id(payload, "CustomerRef"),
            "customer_name": _ref_name(payload, "CustomerRef"),
        })
    elif entity == "Bill":
        row.update({
            "doc_number": payload.get("DocNumber"),
            "due_date": payload.get("DueDate"),
            "total_amt": payload.get("TotalAmt"),
            "balance": payload.get("Balance"),
            "vendor_id": _ref_id(payload, "VendorRef"),
            "vendor_name": _ref_name(payload, "VendorRef"),
        })
    elif entity == "Payment":
        row.update({
            "total_amt": payload.get("TotalAmt"),
            "customer_id": _ref_id(payload, "CustomerRef"),
            "customer_name": _ref_name(payload, "CustomerRef"),
        })
    elif entity == "Purchase":
        row.update({
            "total_amt": payload.get("TotalAmt"),
            "payment_type": payload.get("PaymentType"),
            "vendor_id": _ref_id(payload, "EntityRef"),
            "vendor_name": _ref_name(payload, "EntityRef"),
        })
    elif entity == "PurchaseOrder":
        row.update({
            "doc_number": payload.get("DocNumber"),
            "total_amt": payload.get("TotalAmt"),
            "po_status": payload.get("POStatus"),
            "vendor_id": _ref_id(payload, "VendorRef"),
            "vendor_name": _ref_name(payload, "VendorRef"),
        })
    elif entity == "BillPayment":
        row.update({
            "total_amt": payload.get("TotalAmt"),
            "vendor_id": _ref_id(payload, "VendorRef"),
            "vendor_name": _ref_name(payload, "VendorRef"),
        })
    elif entity == "CreditMemo":
        row.update({
            "doc_number": payload.get("DocNumber"),
            "total_amt": payload.get("TotalAmt"),
            "balance": payload.get("Balance"),
            "customer_id": _ref_id(payload, "CustomerRef"),
            "customer_name": _ref_name(payload, "CustomerRef"),
        })
    elif entity == "Customer":
        row.update({
            "display_name": payload.get("DisplayName"),
            "active": payload.get("Active"),
            "balance": payload.get("Balance"),
        })
    elif entity == "Class":
        row.update({
            "name": payload.get("Name"),
            "active": payload.get("Active"),
            "fully_qualified_name": payload.get("FullyQualifiedName"),
        })
    elif entity == "Department":
        row.update({
            "name": payload.get("Name"),
            "active": payload.get("Active"),
            "fully_qualified_name": payload.get("FullyQualifiedName"),
        })

    return row


def _line_detail(line: dict) -> dict:
    return line.get("AccountBasedExpenseLineDetail") or line.get("ItemBasedExpenseLineDetail") or {}


def purchase_lines(realm_id: str, purchase: dict) -> list[dict]:
    purchase_id = str(purchase["Id"]) if purchase.get("Id") is not None else None
    lines: list[dict] = []

    for line in purchase.get("Line") or []:
        detail = _line_detail(line)
        account_ref = detail.get("AccountRef") or {}
        customer_ref = detail.get("CustomerRef") or {}
        item_ref = detail.get("ItemRef") or {}

        lines.append({
            "realm_id": realm_id,
            "purchase_id": purchase_id,
            "line_id": str(line["Id"]) if line.get("Id") is not None else None,
            "amount": line.get("Amount"),
            "account_id": str(account_ref["value"]) if account_ref.get("value") is not None else None,
            "account_name": account_ref.get("name"),
            "customer_id": str(customer_ref["value"]) if customer_ref.get("value") is not None else None,
            "item_id": str(item_ref["value"]) if item_ref.get("value") is not None else None,
        })

    logger.debug(
        "purchase_lines purchase_id=%s realm_id=%s line_count=%s",
        purchase_id,
        realm_id,
        len(lines),
    )
    return lines


def payment_links(realm_id: str, payment: dict) -> list[dict]:
    payment_id = str(payment["Id"]) if payment.get("Id") is not None else None
    links: list[dict] = []

    for line in payment.get("Line") or []:
        amount = line.get("Amount")
        for linked in line.get("LinkedTxn") or []:
            links.append({
                "realm_id": realm_id,
                "from_type": "Payment",
                "from_id": payment_id,
                "to_type": linked.get("TxnType"),
                "to_id": str(linked["TxnId"]) if linked.get("TxnId") is not None else None,
                "amount": amount,
            })

    logger.debug(
        "payment_links payment_id=%s realm_id=%s link_count=%s",
        payment_id,
        realm_id,
        len(links),
    )
    return links
