from app.financial.qb_map import (
    entity_row,
    is_qbo_deleted,
    purchase_lines,
    payment_links,
    params_hash,
)


def test_deleted_flag():
    assert is_qbo_deleted({"status": "Deleted", "Id": "9"}) is True
    assert is_qbo_deleted({"Id": "9", "TotalAmt": 1}) is False


def test_invoice_row_typed_fields():
    row = entity_row("r1", "Invoice", {
        "Id": "12",
        "SyncToken": "3",
        "DocNumber": "1001",
        "TxnDate": "2026-01-15",
        "DueDate": "2026-02-14",
        "TotalAmt": 250.5,
        "Balance": 40,
        "CustomerRef": {"value": "C1", "name": "Acme"},
        "MetaData": {"LastUpdatedTime": "2026-01-16T12:00:00-07:00"},
    }, synced_at="2026-08-13T08:00:00+00:00")
    assert row["qbo_id"] == "12"
    assert row["customer_name"] == "Acme"
    assert row["balance"] == 40
    assert row["is_deleted"] is False
    assert row["raw"]["Id"] == "12"


def test_purchase_lines_extract_customer_and_account():
    purchase = {
        "Id": "p1",
        "Line": [
            {
                "Id": "1",
                "Amount": 10,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "A1", "name": "COSS Video"},
                    "CustomerRef": {"value": "C1"},
                },
            },
            {
                "Id": "2",
                "Amount": 5,
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "A2", "name": "Office"},
                },
            },
        ],
    }
    lines = purchase_lines("r1", purchase)
    assert lines[0]["customer_id"] == "C1"
    assert lines[1]["customer_id"] is None
    assert lines[1]["account_name"] == "Office"


def test_payment_links():
    payment = {
        "Id": "pay1",
        "Line": [{"Amount": 80, "LinkedTxn": [{"TxnType": "Invoice", "TxnId": "12"}]}],
    }
    links = payment_links("r1", payment)
    assert links == [{
        "realm_id": "r1",
        "from_type": "Payment",
        "from_id": "pay1",
        "to_type": "Invoice",
        "to_id": "12",
        "amount": 80,
    }]


def test_params_hash_stable():
    assert params_hash({"b": 1, "a": 2}) == params_hash({"a": 2, "b": 1})
