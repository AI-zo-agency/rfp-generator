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
  /**
   * Forward-looking figures. `year` and `quarter` are computed in Python;
   * `llm` is Gemini's, which won every scored horizon in testing. Both are
   * shown for the year so a disagreement between them is visible rather than
   * averaged away.
   */
  forecast: {
    as_of: string;
    year: {
      method: string;
      months_booked: number;
      ytd: number;
      point: number;
      expected_error_pct: number;
      year: number;
    } | null;
    quarter: {
      method: string;
      point: number;
      monthly_basis: number;
      expected_error_pct: number;
      /** ~19% error. Do not render without saying so. */
      low_confidence: boolean;
      months_of_history: number;
    } | null;
    /** Deliberately absent — best measured monthly error was 19.8%. */
    month: null;
    month_omitted_reason: string;
    llm: {
      cash_13w: {
        weeks: {
          week: number;
          ending: string;
          from_open_invoices: number;
          from_new_billing: number;
          outflow: number;
          closing_balance: number;
        }[];
        trough: { amount: number; week: number } | null;
        low: number | null;
        high: number | null;
        assumptions: string | null;
        risks: string[] | null;
      } | null;
      year: {
        reasoning: string | null;
        point: number;
        low: number | null;
        high: number | null;
        remaining_months: number | null;
        confidence: "low" | "medium" | "high" | null;
      } | null;
      /**
       * The same forecast in plain English, written by the prose model rather
       * than the forecasting one. This is what the tab shows; `assumptions` and
       * `reasoning` above are the forecaster's own working and are too
       * technical for the person reading this screen.
       */
      plain: {
        cash: string | null;
        year: string | null;
        watch: string | null;
      } | null;
    } | null;
    llm_as_of?: string | null;
    llm_model?: string | null;
    /** Last night's model run failed; the figures are older than the ledger. */
    llm_stale?: boolean;
  } | null;
  /**
   * Bills arrive after the month they belong to, so a recent month's cost is
   * incomplete and its margin reads high. `adjusted_gross_margin_pct` is the
   * same span with the missing cost estimated in — never a ledger actual.
   */
  cost_completeness: {
    as_of: string;
    curve: { days: number; pct: number }[];
    /** Last month whose own margin can be quoted without a caveat. */
    settled_through: string | null;
    unsettled_months: string[];
    missing_cost: number;
    reported_gross_margin_pct: number | null;
    adjusted_gross_margin_pct: number | null;
    overstated_points: number | null;
    months: {
      month: string;
      age_days: number;
      completeness_pct: number;
      booked_cost: number;
      expected_cost: number | null;
      settled: boolean;
    }[];
  } | null;
}
