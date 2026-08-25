export type Bucket = { label: string; amount: number; count?: number; pct?: number };
export type MonthAmt = { month: string; amount: number };

export type Severity = "critical" | "warn" | "info";

export interface Signal {
  id: string;
  severity: Severity;
  /** Plain-English statement of the problem. No jargon, no metric names. */
  headline: string;
  /** The one number that sizes the problem. Pre-formatted by the backend. */
  figure?: string;
  /** Why it matters or what to do. One clause. */
  detail?: string;
  /** Tab id to jump to for the underlying rows. */
  go_to?: string;
}

export interface QuickBooksOverview {
  year: number;
  generated_at: string;
  as_of?: string;
  synced_at?: string;
  sync_status?: "ok" | "failed" | "backfill_pending" | "missing";
  errors: Record<string, string>;
  signals: Signal[];
  company: {
    company_name: string;
    legal_name: string;
    city: string;
    state: string;
    sku: string;
  } | null;
  ar: {
    total: number;
    invoice_count: number;
    overdue_total: number;
    buckets: Bucket[];
    clients: { client: string; amount: number; invoices: number; oldest_days: number }[];
  } | null;
  ap: {
    total: number;
    bill_count: number;
    buckets: Bucket[];
    vendors: { vendor: string; amount: number }[];
  } | null;
  revenue_by_class: {
    matrix: { parent: string; segment: string; amount: number }[];
    parents: string[];
    segments: string[];
    unclassified: number;
    total: number;
    coverage_pct: number;
  } | null;
  by_account_manager: {
    managers: { manager: string; income: number; net: number; is_overhead: boolean }[];
  } | null;
  client_profitability: {
    clients: {
      client: string;
      income: number;
      expense: number;
      net: number;
      margin_pct: number | null;
    }[];
    attributed_expense: number;
  } | null;
  monthly_trend: {
    months: MonthAmt[];
    total: number;
    peak: number;
    last_booked_month: string | null;
  } | null;
  pl_summary: {
    income: number | null;
    cost_of_services: number | null;
    gross_profit: number | null;
    gross_margin_pct: number | null;
    net_income: number | null;
  } | null;
  unattached_cost: {
    purchase_count: number;
    purchase_total: number;
    unattached_count: number;
    unattached_pct: number;
    cost_of_service_unattached: number;
    accounts: { account: string; amount: number; is_cost_of_service: boolean }[];
  } | null;
  activity: {
    since: string;
    total: number;
    entities: { entity: string; changed: number }[];
  } | null;
  cash_collections: {
    total_collected: number;
    payment_count: number;
    by_month: MonthAmt[];
    top_payers: { customer: string; amount: number }[];
  } | null;
  billing_vs_cash: {
    invoiced_total: number;
    collected_total: number;
    open_ar: number;
    collection_rate_pct: number;
    invoice_count: number;
    payment_count: number;
    by_month: { month: string; invoiced: number; collected: number }[];
  } | null;
  dso: {
    dso_days: number | null;
    sample_size: number;
    slowest_clients: { client: string; avg_days: number; amount: number }[];
  } | null;
  aged_ar_detail: {
    report_date: string;
    columns: string[];
    row_count: number;
    source: string;
  } | null;
  purchase_orders: {
    po_count: number;
    open_count: number;
    open_total: number;
    ytd_total: number;
    vendors: { vendor: string; amount: number }[];
  } | null;
  expenses_by_vendor: {
    total: number;
    vendor_count: number;
    top3_concentration_pct: number;
    vendors: { vendor: string; amount: number }[];
  } | null;
  bill_payments: {
    total_paid: number;
    payment_count: number;
    by_month: MonthAmt[];
  } | null;
  customers: {
    count: number;
    customers: { id: string; display_name: string; company_name: string; balance: number }[];
  } | null;
  sales_by_customer: {
    total: number;
    clients: { client: string; amount: number }[];
  } | null;
  credit_memos: {
    total: number;
    count: number;
    clients: { client: string; amount: number }[];
  } | null;
  class_coverage: {
    class_count: number;
    classes: string[];
    coverage_pct: number;
    unclassified: number;
    total: number;
  } | null;
  department_coverage: {
    department_count: number;
    departments: string[];
    overhead_income: number;
    overhead_pct: number;
    manager_count: number;
  } | null;
  liquidity: {
    as_of: string;
    cash: number;
    net_cash_change: number | null;
  } | null;
}
