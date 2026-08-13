from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.financial import qb_panels_from_db as panels


def test_panels_module_does_not_call_live_qbo():
    source = Path(panels.__file__).read_text()
    assert "quickbooks.query" not in source
    assert "quickbooks.report" not in source
    assert "quickbooks.cdc" not in source
    assert "date.today" not in source


def test_ar_aging_uses_as_of_not_today():
    invoices = [{
        "due_date": "2026-08-01",
        "txn_date": "2026-07-01",
        "balance": 100,
        "customer_name": "Acme",
        "is_deleted": False,
    }]
    with patch.object(panels, "list_open_invoices", return_value=invoices):
        result = panels.ar_aging("r1", as_of=date(2026, 8, 13))
    overdue = next(b for b in result["buckets"] if b["label"] == "1-30 days")
    assert overdue["amount"] == 100
    assert result["overdue_total"] == 100


def test_ar_aging_as_of_shifts_bucket_off_wall_clock():
    """as_of=2026-09-15 is 45 days after due_date → 31-60, not today's 1-30."""
    invoices = [{
        "due_date": "2026-08-01",
        "txn_date": "2026-07-01",
        "balance": 100,
        "customer_name": "Acme",
        "is_deleted": False,
    }]
    with patch.object(panels, "list_open_invoices", return_value=invoices):
        result = panels.ar_aging("r1", as_of=date(2026, 9, 15))
    bucket = next(b for b in result["buckets"] if b["label"] == "31-60 days")
    assert bucket["amount"] == 100
    assert result["overdue_total"] == 100


def _stub_lists(monkeypatch, empty: list) -> None:
    monkeypatch.setattr(panels, "list_open_invoices", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_open_bills", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_invoices", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_payments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchases", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchase_lines", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_txn_links", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchase_orders", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_bill_payments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_credit_memos", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_customers", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_classes", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_departments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "get_report_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(panels, "get_company_info", lambda *a, **k: None)
    monkeypatch.setattr(panels, "count_activity", lambda *a, **k: {"since": "", "total": 0, "entities": []})


def test_build_overview_does_not_call_quickbooks(monkeypatch):
    import app.financial.quickbooks as qb
    monkeypatch.setattr(qb, "query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live query")))
    monkeypatch.setattr(qb, "report", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live report")))
    monkeypatch.setattr(qb, "cdc", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live cdc")))
    empty: list = []
    monkeypatch.setattr(panels, "list_open_invoices", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_open_bills", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_invoices", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_payments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchases", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchase_lines", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_txn_links", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_purchase_orders", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_bill_payments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_credit_memos", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_customers", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_classes", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "list_departments", lambda *a, **k: empty)
    monkeypatch.setattr(panels, "get_report_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(panels, "get_company_info", lambda *a, **k: None)
    monkeypatch.setattr(panels, "count_activity", lambda *a, **k: {"since": "", "total": 0, "entities": []})
    out = panels.build_overview("r1", 2026, as_of=date(2026, 8, 13), activity_since="2026-08-01T00:00:00-07:00")
    assert out["year"] == 2026
    assert "ar" in out


def test_build_overview_missing_snapshot_sets_error(monkeypatch):
    _stub_lists(monkeypatch, [])
    out = panels.build_overview(
        "r1",
        2026,
        as_of=date(2026, 8, 13),
        activity_since="2026-08-01T00:00:00-07:00",
    )
    assert out["revenue_by_class"] is None
    assert "revenue_by_class" in out["errors"]
    assert out["ar"] is not None
