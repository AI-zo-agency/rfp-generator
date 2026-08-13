from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260813_quickbooks_mirror.sql"
)

TABLES = [
    "qb_invoices",
    "qb_bills",
    "qb_payments",
    "qb_purchases",
    "qb_purchase_orders",
    "qb_bill_payments",
    "qb_credit_memos",
    "qb_customers",
    "qb_classes",
    "qb_departments",
    "qb_purchase_lines",
    "qb_txn_links",
    "qb_report_snapshots",
    "qb_company_info",
    "qb_panel_cache",
    "qb_oauth_tokens",
    "qb_sync_state",
    "qb_sync_runs",
    "qb_backfill_progress",
]

INDEXES = [
    "idx_qb_invoices_txn_date",
    "idx_qb_invoices_qbo_updated_at",
    "idx_qb_invoices_synced_at",
    "idx_qb_invoices_open_balance",
    "idx_qb_invoices_due_date",
    "idx_qb_invoices_customer_id",
    "idx_qb_invoices_customer_name",
    "idx_qb_invoices_doc_number",
    "idx_qb_bills_txn_date",
    "idx_qb_bills_qbo_updated_at",
    "idx_qb_bills_synced_at",
    "idx_qb_bills_open_balance",
    "idx_qb_bills_due_date",
    "idx_qb_bills_vendor_id",
    "idx_qb_bills_vendor_name",
    "idx_qb_bills_doc_number",
    "idx_qb_payments_txn_date",
    "idx_qb_payments_qbo_updated_at",
    "idx_qb_payments_synced_at",
    "idx_qb_payments_customer_id",
    "idx_qb_payments_customer_name",
    "idx_qb_purchases_txn_date",
    "idx_qb_purchases_qbo_updated_at",
    "idx_qb_purchases_synced_at",
    "idx_qb_purchases_vendor_id",
    "idx_qb_purchases_vendor_name",
    "idx_qb_purchases_payment_type",
    "idx_qb_purchase_orders_txn_date",
    "idx_qb_purchase_orders_qbo_updated_at",
    "idx_qb_purchase_orders_synced_at",
    "idx_qb_purchase_orders_po_status",
    "idx_qb_purchase_orders_vendor_id",
    "idx_qb_purchase_orders_vendor_name",
    "idx_qb_purchase_orders_doc_number",
    "idx_qb_bill_payments_txn_date",
    "idx_qb_bill_payments_qbo_updated_at",
    "idx_qb_bill_payments_synced_at",
    "idx_qb_bill_payments_vendor_id",
    "idx_qb_bill_payments_vendor_name",
    "idx_qb_credit_memos_txn_date",
    "idx_qb_credit_memos_qbo_updated_at",
    "idx_qb_credit_memos_synced_at",
    "idx_qb_credit_memos_customer_id",
    "idx_qb_credit_memos_customer_name",
    "idx_qb_credit_memos_balance",
    "idx_qb_credit_memos_doc_number",
    "idx_qb_customers_active",
    "idx_qb_customers_display_name",
    "idx_qb_customers_qbo_updated_at",
    "idx_qb_classes_active",
    "idx_qb_classes_name",
    "idx_qb_classes_fully_qualified_name",
    "idx_qb_departments_active",
    "idx_qb_departments_name",
    "idx_qb_departments_fully_qualified_name",
    "idx_qb_purchase_lines_customer_id",
    "idx_qb_purchase_lines_account_id",
    "idx_qb_purchase_lines_account_name",
    "idx_qb_purchase_lines_item_id",
    "idx_qb_purchase_lines_unattached",
    "idx_qb_txn_links_to",
    "idx_qb_report_snapshots_year",
    "idx_qb_panel_cache_computed_at",
    "idx_qb_sync_state_lease_expires_at",
    "idx_qb_sync_runs_started_at",
    "idx_qb_sync_runs_status_started_at",
]


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_migration_creates_all_tables():
    sql = MIGRATION.read_text()
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table


def test_migration_creates_named_indexes():
    sql = MIGRATION.read_text()
    for name in INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in sql, name


def test_migration_enables_rls_and_revokes_anon():
    sql = MIGRATION.read_text()
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql, table
        assert f"REVOKE ALL ON TABLE {table} FROM anon, authenticated" in sql, table
