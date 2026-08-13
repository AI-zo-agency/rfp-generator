"""QuickBooks panel transforms and read-only Accounting API guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.financial import quickbooks as qb


def test_accounting_client_is_get_only():
    """No write verbs against the Accounting API in the QB client module."""
    source = Path(qb.__file__).read_text()
    # Transport uses httpx.get only; OAuth refresh lives in quickbooks_oauth.
    assert "httpx.get(" in source
    for verb in ("httpx.post(", "httpx.put(", "httpx.patch(", "httpx.delete("):
        assert verb not in source, f"Unexpected write call {verb} in quickbooks.py"


def test_cash_collections_aggregates_payments():
    payments = [
        {
            "TotalAmt": 100,
            "TxnDate": "2026-01-15",
            "CustomerRef": {"name": "Alpha"},
        },
        {
            "TotalAmt": 50.5,
            "TxnDate": "2026-01-20",
            "CustomerRef": {"name": "Alpha"},
        },
        {
            "TotalAmt": 200,
            "TxnDate": "2026-03-01",
            "CustomerRef": {"name": "Beta"},
        },
    ]
    with patch.object(qb, "query", return_value=payments):
        result = qb.cash_collections(2026)
    assert result["total_collected"] == 350.5
    assert result["payment_count"] == 3
    assert result["by_month"][0]["amount"] == 150.5
    assert result["by_month"][2]["amount"] == 200.0
    assert result["top_payers"][0]["customer"] == "Beta"


def test_billing_vs_cash_rates():
    invoices = [
        {"TotalAmt": 1000, "Balance": 200, "TxnDate": "2026-02-10"},
        {"TotalAmt": 500, "Balance": 0, "TxnDate": "2026-02-12"},
    ]
    payments = [
        {"TotalAmt": 800, "TxnDate": "2026-02-28"},
        {"TotalAmt": 400, "TxnDate": "2026-03-05"},
    ]

    def fake_query(sql: str, key: str):
        if key == "Invoice":
            return invoices
        return payments

    with patch.object(qb, "query", side_effect=fake_query):
        result = qb.billing_vs_cash(2026)
    assert result["invoiced_total"] == 1500.0
    assert result["collected_total"] == 1200.0
    assert result["open_ar"] == 200.0
    assert result["collection_rate_pct"] == 80.0
    assert result["by_month"][1]["invoiced"] == 1500.0
    assert result["by_month"][1]["collected"] == 800.0


def test_dso_from_linked_txns():
    invoices = [
        {"Id": "1", "TxnDate": "2026-01-01", "TotalAmt": 100},
        {"Id": "2", "TxnDate": "2026-01-10", "TotalAmt": 200},
    ]
    payments = [
        {
            "TxnDate": "2026-01-31",
            "CustomerRef": {"name": "Slow Co"},
            "Line": [
                {
                    "Amount": 100,
                    "LinkedTxn": [{"TxnType": "Invoice", "TxnId": "1"}],
                }
            ],
        },
        {
            "TxnDate": "2026-01-20",
            "CustomerRef": {"name": "Fast Co"},
            "Line": [
                {
                    "Amount": 200,
                    "LinkedTxn": [{"TxnType": "Invoice", "TxnId": "2"}],
                }
            ],
        },
    ]

    def fake_query(sql: str, key: str):
        return invoices if key == "Invoice" else payments

    with patch.object(qb, "query", side_effect=fake_query):
        result = qb.dso(2026)
    assert result["sample_size"] == 2
    assert result["dso_days"] == 20.0  # (30 + 10) / 2
    assert result["slowest_clients"][0]["client"] == "Slow Co"
    assert result["slowest_clients"][0]["avg_days"] == 30.0


def test_credit_memos_totals():
    rows = [
        {"TotalAmt": 50, "CustomerRef": {"name": "A"}},
        {"TotalAmt": 25, "CustomerRef": {"name": "B"}},
        {"TotalAmt": 10, "CustomerRef": {"name": "A"}},
    ]
    with patch.object(qb, "query", return_value=rows):
        result = qb.credit_memos(2026)
    assert result["total"] == 85.0
    assert result["count"] == 3
    assert result["clients"][0]["client"] == "A"
    assert result["clients"][0]["amount"] == 60.0
