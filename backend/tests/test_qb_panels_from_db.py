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
    monkeypatch.setattr(panels, "list_bills", lambda *a, **k: empty)
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
    monkeypatch.setattr(panels, "list_bills", lambda *a, **k: empty)
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
    assert out["pl_summary"] is None
    assert "pl_summary" in out["errors"]
    assert out["ar"] is not None
    assert out["sales_by_customer"] is not None
    assert "sales_by_customer" not in out["errors"]
    assert out["expenses_by_vendor"] is not None
    assert "expenses_by_vendor" not in out["errors"]


def test_sales_by_customer_falls_back_to_customer_income():
    income = {
        "Rows": {
            "Row": [{
                "ColData": [
                    {"value": "Acme"},
                    {"value": "1200.00"},
                    {"value": "100.00"},
                    {"value": "1100.00"},
                ]
            }]
        }
    }

    def fake_snapshot(_realm, report_name, _year, _params):
        if report_name in ("CustomerSales", "SalesByCustomer"):
            raise LookupError(f"missing {report_name}")
        if report_name == "CustomerIncome":
            return income
        raise LookupError(report_name)

    with patch.object(panels, "_snapshot_payload", side_effect=fake_snapshot):
        result = panels.sales_by_customer("r1", 2026)
    assert result["clients"][0]["client"] == "Acme"
    assert result["clients"][0]["amount"] == 1200.0
    assert result["total"] == 1200.0


def test_sales_by_customer_prefers_customer_sales_snapshot():
    sales = {
        "Rows": {
            "Row": [{
                "ColData": [
                    {"value": "Acme"},
                    {"value": "900.00"},
                ]
            }]
        }
    }

    def fake_snapshot(_realm, report_name, _year, _params):
        if report_name == "CustomerSales":
            return sales
        raise LookupError(report_name)

    with patch.object(panels, "_snapshot_payload", side_effect=fake_snapshot):
        result = panels.sales_by_customer("r1", 2026)
    assert result["clients"][0] == {"client": "Acme", "amount": 900.0}
    assert result["total"] == 900.0


def test_sales_by_customer_falls_back_to_invoices():
    invoices = [
        {"customer_name": "Beta", "total_amt": 400, "is_deleted": False},
        {"customer_name": "Acme", "total_amt": 800, "is_deleted": False},
        {"customer_name": "Beta", "total_amt": 50, "is_deleted": False},
    ]

    def fake_snapshot(*_args, **_kwargs):
        raise LookupError("missing snapshot")

    with patch.object(panels, "_snapshot_payload", side_effect=fake_snapshot), \
            patch.object(panels, "list_invoices", return_value=invoices):
        result = panels.sales_by_customer("r1", 2026)
    assert result["clients"][0] == {"client": "Acme", "amount": 800.0}
    assert result["clients"][1] == {"client": "Beta", "amount": 450.0}
    assert result["total"] == 1250.0


def test_expenses_by_vendor_prefers_vendor_expenses_snapshot():
    expenses = {
        "Rows": {
            "Row": [{
                "ColData": [
                    {"value": "Paper Co"},
                    {"value": "125.00"},
                ]
            }]
        }
    }

    def fake_snapshot(_realm, report_name, _year, _params):
        if report_name == "VendorExpenses":
            return expenses
        raise LookupError(report_name)

    with patch.object(panels, "_snapshot_payload", side_effect=fake_snapshot):
        result = panels.expenses_by_vendor("r1", 2026)
    assert result["vendors"][0] == {"vendor": "Paper Co", "amount": 125.0}
    assert result["total"] == 125.0


def test_expenses_by_vendor_falls_back_to_bills_and_purchases():
    bills = [{"vendor_name": "Paper Co", "total_amt": 300}]
    purchases = [
        {"vendor_name": "Paper Co", "total_amt": 50},
        {"vendor_name": "Ink LLC", "total_amt": 200},
    ]

    def fake_snapshot(*_args, **_kwargs):
        raise LookupError("missing ExpensesByVendorSummary")

    with patch.object(panels, "_snapshot_payload", side_effect=fake_snapshot), \
            patch.object(panels, "list_bills", return_value=bills), \
            patch.object(panels, "list_purchases", return_value=purchases):
        result = panels.expenses_by_vendor("r1", 2026)
    assert result["vendors"][0] == {"vendor": "Paper Co", "amount": 350.0}
    assert result["vendors"][1] == {"vendor": "Ink LLC", "amount": 200.0}
    assert result["total"] == 550.0
    assert result["top3_concentration_pct"] == 100.0


def test_expenses_from_snapshot_keeps_totally_promotional_and_report_total():
    payload = {
        "Rows": {
            "Row": [
                {"ColData": [{"value": "Paper Co"}, {"value": "100.00"}]},
                {"ColData": [{"value": "Totally Promotional"}, {"value": "324.00"}]},
                {"ColData": [{"value": "TOTAL"}, {"value": "917357.51"}]},
            ]
        }
    }
    result = panels._expenses_from_snapshot(payload)
    assert result["total"] == 917357.51
    vendors = {row["vendor"]: row["amount"] for row in result["vendors"]}
    assert vendors["Totally Promotional"] == 324.0
    assert vendors["Paper Co"] == 100.0
    assert result["vendor_count"] == 2


def test_pl_summary_reads_total_column_not_month():
    payload = {
        "Rows": {
            "Row": [
                {"ColData": [
                    {"value": "Total Income"},
                    {"value": "100.00"},
                    {"value": "1000.00"},
                ]},
                {"ColData": [
                    {"value": "Total Cost of Goods Sold"},
                    {"value": "40.00"},
                    {"value": "400.00"},
                ]},
                {"ColData": [
                    {"value": "Gross Profit"},
                    {"value": "60.00"},
                    {"value": "600.00"},
                ]},
                {"ColData": [
                    {"value": "Net Income"},
                    {"value": "10.00"},
                    {"value": "130.00"},
                ]},
            ]
        }
    }
    with patch.object(panels, "_snapshot_payload", return_value=payload):
        result = panels.pl_summary("r1", 2026)
    assert result["income"] == 1000.0
    assert result["cost_of_services"] == 400.0
    assert result["gross_profit"] == 600.0
    assert result["gross_margin_pct"] == 60.0
    assert result["net_income"] == 130.0


def test_pl_summary_gross_profit_falls_back_to_income_minus_cogs():
    payload = {
        "Rows": {
            "Row": [
                {"ColData": [{"value": "Total Income"}, {"value": "1000.00"}]},
                {"ColData": [{"value": "Total Cost of Goods Sold"}, {"value": "400.00"}]},
                {"ColData": [{"value": "Net Income"}, {"value": "130.00"}]},
            ]
        }
    }
    with patch.object(panels, "_snapshot_payload", return_value=payload):
        result = panels.pl_summary("r1", 2026)
    assert result["gross_profit"] == 600.0
    assert result["gross_margin_pct"] == 60.0
    assert result["net_income"] == 130.0


def test_pl_summary_missing_net_income_is_none():
    payload = {
        "Rows": {
            "Row": [
                {"ColData": [{"value": "Total Income"}, {"value": "1000.00"}]},
                {"ColData": [{"value": "Total Cost of Goods Sold"}, {"value": "400.00"}]},
                {"ColData": [{"value": "Gross Profit"}, {"value": "600.00"}]},
            ]
        }
    }
    with patch.object(panels, "_snapshot_payload", return_value=payload):
        result = panels.pl_summary("r1", 2026)
    assert result["net_income"] is None
    assert result["income"] == 1000.0
    assert result["gross_profit"] == 600.0
