-- QuickBooks nightly mirror schema.
-- Apply in Supabase SQL editor if migration tooling is not used.

-- Entity tables (transactional)

CREATE TABLE IF NOT EXISTS qb_invoices (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  doc_number TEXT,
  due_date DATE,
  total_amt NUMERIC(15,2),
  balance NUMERIC(15,2),
  customer_id TEXT,
  customer_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_bills (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  doc_number TEXT,
  due_date DATE,
  total_amt NUMERIC(15,2),
  balance NUMERIC(15,2),
  vendor_id TEXT,
  vendor_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_payments (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  total_amt NUMERIC(15,2),
  customer_id TEXT,
  customer_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_purchases (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  total_amt NUMERIC(15,2),
  payment_type TEXT,
  vendor_id TEXT,
  vendor_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_purchase_orders (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  doc_number TEXT,
  total_amt NUMERIC(15,2),
  po_status TEXT,
  vendor_id TEXT,
  vendor_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_bill_payments (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  total_amt NUMERIC(15,2),
  vendor_id TEXT,
  vendor_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_credit_memos (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  doc_number TEXT,
  total_amt NUMERIC(15,2),
  balance NUMERIC(15,2),
  customer_id TEXT,
  customer_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

-- Entity tables (lists)

CREATE TABLE IF NOT EXISTS qb_customers (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  display_name TEXT,
  active BOOLEAN,
  balance NUMERIC(15,2),
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_classes (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  name TEXT,
  active BOOLEAN,
  fully_qualified_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

CREATE TABLE IF NOT EXISTS qb_departments (
  realm_id TEXT NOT NULL,
  qbo_id TEXT NOT NULL,
  sync_token TEXT,
  is_deleted BOOLEAN NOT NULL DEFAULT false,
  txn_date DATE,
  qbo_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  name TEXT,
  active BOOLEAN,
  fully_qualified_name TEXT,
  PRIMARY KEY (realm_id, qbo_id)
);

-- Child / control tables

CREATE TABLE IF NOT EXISTS qb_purchase_lines (
  realm_id TEXT NOT NULL,
  purchase_id TEXT NOT NULL,
  line_id TEXT NOT NULL,
  amount NUMERIC(15,2),
  account_id TEXT,
  account_name TEXT,
  customer_id TEXT,
  item_id TEXT,
  PRIMARY KEY (realm_id, purchase_id, line_id)
);

CREATE TABLE IF NOT EXISTS qb_txn_links (
  realm_id TEXT NOT NULL,
  from_type TEXT NOT NULL,
  from_id TEXT NOT NULL,
  to_type TEXT NOT NULL,
  to_id TEXT NOT NULL,
  amount NUMERIC(15,2),
  PRIMARY KEY (realm_id, from_type, from_id, to_type, to_id)
);

CREATE TABLE IF NOT EXISTS qb_report_snapshots (
  realm_id TEXT NOT NULL,
  report_name TEXT NOT NULL,
  year INTEGER NOT NULL,
  params_hash TEXT NOT NULL,
  params JSONB NOT NULL,
  payload JSONB NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (realm_id, report_name, year, params_hash)
);

CREATE TABLE IF NOT EXISTS qb_company_info (
  realm_id TEXT PRIMARY KEY,
  company_name TEXT,
  legal_name TEXT,
  city TEXT,
  state TEXT,
  fiscal_year_start TEXT,
  start_date TEXT,
  sku TEXT,
  raw JSONB NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS qb_panel_cache (
  realm_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  payload JSONB NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (realm_id, year)
);

CREATE TABLE IF NOT EXISTS qb_oauth_tokens (
  realm_id TEXT PRIMARY KEY,
  refresh_token TEXT NOT NULL,
  access_token TEXT,
  access_expires_at TIMESTAMPTZ,
  x_refresh_token_expires_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS qb_sync_state (
  realm_id TEXT PRIMARY KEY,
  cdc_cursor TIMESTAMPTZ,
  backfill_completed_at TIMESTAMPTZ,
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  last_started_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ,
  last_error TEXT,
  last_mode TEXT
);

CREATE TABLE IF NOT EXISTS qb_sync_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  realm_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  entities_upserted JSONB,
  error TEXT
);

CREATE TABLE IF NOT EXISTS qb_backfill_progress (
  realm_id TEXT NOT NULL,
  entity TEXT NOT NULL,
  startposition INTEGER NOT NULL DEFAULT 1,
  completed BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (realm_id, entity)
);

-- Indexes: qb_invoices

CREATE INDEX IF NOT EXISTS idx_qb_invoices_txn_date
  ON qb_invoices (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_qbo_updated_at
  ON qb_invoices (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_synced_at
  ON qb_invoices (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_invoices_open_balance
  ON qb_invoices (realm_id, balance) WHERE is_deleted = false AND balance > 0;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_due_date
  ON qb_invoices (realm_id, due_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_customer_id
  ON qb_invoices (realm_id, customer_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_customer_name
  ON qb_invoices (realm_id, customer_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_invoices_doc_number
  ON qb_invoices (realm_id, doc_number) WHERE is_deleted = false;

-- Indexes: qb_bills

CREATE INDEX IF NOT EXISTS idx_qb_bills_txn_date
  ON qb_bills (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bills_qbo_updated_at
  ON qb_bills (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bills_synced_at
  ON qb_bills (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_bills_open_balance
  ON qb_bills (realm_id, balance) WHERE is_deleted = false AND balance > 0;
CREATE INDEX IF NOT EXISTS idx_qb_bills_due_date
  ON qb_bills (realm_id, due_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bills_vendor_id
  ON qb_bills (realm_id, vendor_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bills_vendor_name
  ON qb_bills (realm_id, vendor_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bills_doc_number
  ON qb_bills (realm_id, doc_number) WHERE is_deleted = false;

-- Indexes: qb_payments

CREATE INDEX IF NOT EXISTS idx_qb_payments_txn_date
  ON qb_payments (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_payments_qbo_updated_at
  ON qb_payments (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_payments_synced_at
  ON qb_payments (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_payments_customer_id
  ON qb_payments (realm_id, customer_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_payments_customer_name
  ON qb_payments (realm_id, customer_name) WHERE is_deleted = false;

-- Indexes: qb_purchases

CREATE INDEX IF NOT EXISTS idx_qb_purchases_txn_date
  ON qb_purchases (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchases_qbo_updated_at
  ON qb_purchases (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchases_synced_at
  ON qb_purchases (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_purchases_vendor_id
  ON qb_purchases (realm_id, vendor_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchases_vendor_name
  ON qb_purchases (realm_id, vendor_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchases_payment_type
  ON qb_purchases (realm_id, payment_type) WHERE is_deleted = false;

-- Indexes: qb_purchase_orders

CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_txn_date
  ON qb_purchase_orders (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_qbo_updated_at
  ON qb_purchase_orders (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_synced_at
  ON qb_purchase_orders (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_po_status
  ON qb_purchase_orders (realm_id, po_status) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_vendor_id
  ON qb_purchase_orders (realm_id, vendor_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_vendor_name
  ON qb_purchase_orders (realm_id, vendor_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_purchase_orders_doc_number
  ON qb_purchase_orders (realm_id, doc_number) WHERE is_deleted = false;

-- Indexes: qb_bill_payments

CREATE INDEX IF NOT EXISTS idx_qb_bill_payments_txn_date
  ON qb_bill_payments (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bill_payments_qbo_updated_at
  ON qb_bill_payments (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bill_payments_synced_at
  ON qb_bill_payments (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_bill_payments_vendor_id
  ON qb_bill_payments (realm_id, vendor_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_bill_payments_vendor_name
  ON qb_bill_payments (realm_id, vendor_name) WHERE is_deleted = false;

-- Indexes: qb_credit_memos

CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_txn_date
  ON qb_credit_memos (realm_id, txn_date) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_qbo_updated_at
  ON qb_credit_memos (realm_id, qbo_updated_at) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_synced_at
  ON qb_credit_memos (realm_id, synced_at);
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_customer_id
  ON qb_credit_memos (realm_id, customer_id) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_customer_name
  ON qb_credit_memos (realm_id, customer_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_balance
  ON qb_credit_memos (realm_id, balance) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_credit_memos_doc_number
  ON qb_credit_memos (realm_id, doc_number) WHERE is_deleted = false;

-- Indexes: qb_customers

CREATE INDEX IF NOT EXISTS idx_qb_customers_active
  ON qb_customers (realm_id, active) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_customers_display_name
  ON qb_customers (realm_id, display_name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_customers_qbo_updated_at
  ON qb_customers (realm_id, qbo_updated_at) WHERE is_deleted = false;

-- Indexes: qb_classes

CREATE INDEX IF NOT EXISTS idx_qb_classes_active
  ON qb_classes (realm_id, active) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_classes_name
  ON qb_classes (realm_id, name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_classes_fully_qualified_name
  ON qb_classes (realm_id, fully_qualified_name) WHERE is_deleted = false;

-- Indexes: qb_departments

CREATE INDEX IF NOT EXISTS idx_qb_departments_active
  ON qb_departments (realm_id, active) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_departments_name
  ON qb_departments (realm_id, name) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_qb_departments_fully_qualified_name
  ON qb_departments (realm_id, fully_qualified_name) WHERE is_deleted = false;

-- Indexes: child / control tables

CREATE INDEX IF NOT EXISTS idx_qb_purchase_lines_customer_id
  ON qb_purchase_lines (realm_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_qb_purchase_lines_account_id
  ON qb_purchase_lines (realm_id, account_id);
CREATE INDEX IF NOT EXISTS idx_qb_purchase_lines_account_name
  ON qb_purchase_lines (realm_id, account_name);
CREATE INDEX IF NOT EXISTS idx_qb_purchase_lines_item_id
  ON qb_purchase_lines (realm_id, item_id);
CREATE INDEX IF NOT EXISTS idx_qb_purchase_lines_unattached
  ON qb_purchase_lines (realm_id, purchase_id) WHERE customer_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_qb_txn_links_to
  ON qb_txn_links (realm_id, to_type, to_id);

CREATE INDEX IF NOT EXISTS idx_qb_report_snapshots_year
  ON qb_report_snapshots (realm_id, year);

CREATE INDEX IF NOT EXISTS idx_qb_panel_cache_computed_at
  ON qb_panel_cache (computed_at);

CREATE INDEX IF NOT EXISTS idx_qb_sync_state_lease_expires_at
  ON qb_sync_state (lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_qb_sync_runs_started_at
  ON qb_sync_runs (realm_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_qb_sync_runs_status_started_at
  ON qb_sync_runs (realm_id, status, started_at DESC);

-- Row Level Security

ALTER TABLE qb_invoices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_invoices FROM anon, authenticated;
GRANT ALL ON TABLE qb_invoices TO service_role;

ALTER TABLE qb_bills ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_bills FROM anon, authenticated;
GRANT ALL ON TABLE qb_bills TO service_role;

ALTER TABLE qb_payments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_payments FROM anon, authenticated;
GRANT ALL ON TABLE qb_payments TO service_role;

ALTER TABLE qb_purchases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_purchases FROM anon, authenticated;
GRANT ALL ON TABLE qb_purchases TO service_role;

ALTER TABLE qb_purchase_orders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_purchase_orders FROM anon, authenticated;
GRANT ALL ON TABLE qb_purchase_orders TO service_role;

ALTER TABLE qb_bill_payments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_bill_payments FROM anon, authenticated;
GRANT ALL ON TABLE qb_bill_payments TO service_role;

ALTER TABLE qb_credit_memos ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_credit_memos FROM anon, authenticated;
GRANT ALL ON TABLE qb_credit_memos TO service_role;

ALTER TABLE qb_customers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_customers FROM anon, authenticated;
GRANT ALL ON TABLE qb_customers TO service_role;

ALTER TABLE qb_classes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_classes FROM anon, authenticated;
GRANT ALL ON TABLE qb_classes TO service_role;

ALTER TABLE qb_departments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_departments FROM anon, authenticated;
GRANT ALL ON TABLE qb_departments TO service_role;

ALTER TABLE qb_purchase_lines ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_purchase_lines FROM anon, authenticated;
GRANT ALL ON TABLE qb_purchase_lines TO service_role;

ALTER TABLE qb_txn_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_txn_links FROM anon, authenticated;
GRANT ALL ON TABLE qb_txn_links TO service_role;

ALTER TABLE qb_report_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_report_snapshots FROM anon, authenticated;
GRANT ALL ON TABLE qb_report_snapshots TO service_role;

ALTER TABLE qb_company_info ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_company_info FROM anon, authenticated;
GRANT ALL ON TABLE qb_company_info TO service_role;

ALTER TABLE qb_panel_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_panel_cache FROM anon, authenticated;
GRANT ALL ON TABLE qb_panel_cache TO service_role;

ALTER TABLE qb_oauth_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_oauth_tokens FROM anon, authenticated;
GRANT ALL ON TABLE qb_oauth_tokens TO service_role;

ALTER TABLE qb_sync_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_sync_state FROM anon, authenticated;
GRANT ALL ON TABLE qb_sync_state TO service_role;

ALTER TABLE qb_sync_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_sync_runs FROM anon, authenticated;
GRANT ALL ON TABLE qb_sync_runs TO service_role;

ALTER TABLE qb_backfill_progress ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE qb_backfill_progress FROM anon, authenticated;
GRANT ALL ON TABLE qb_backfill_progress TO service_role;
