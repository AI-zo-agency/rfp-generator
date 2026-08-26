export type AgencyJoinStatus =
  | "confirmed"
  | "suggested"
  | "needs mapping"
  | "internal"
  | "ambiguous"
  | "job override";

export interface AgencyJobRow {
  project_id: string;
  job_label: string;
  project_name: string;
  company_name: string;
  client_name: string;
  status: string;
  health: string;
  hours_mtd_minutes: number;
  billed_ytd: number | null;
  open_ar: number | null;
  join: AgencyJoinStatus;
  client_map_id: string | null;
  link_confidence: string | null;
  via: string | null;
}

export interface AgencyInvoiceException {
  invoice_id: string;
  invoice_number: string | null;
  customer_id: string | null;
  customer_name: string | null;
  txn_date: string | null;
  due_date: string | null;
  total_amt: number;
  open_ar: number;
  /** Invoice state supplied by the overview endpoint; unknown values are rendered neutrally. */
  status?: string;
  /** Every row in this watchlist is intentionally missing a Teamwork relationship. */
  relationship?: "missing relationship";
}

export interface AgencyResolutionOption {
  project_id: string;
  project_name: string;
  company_name: string;
  client_map_id: string | null;
}

export interface AgencyOverview {
  year: number;
  as_of?: string | null;
  position: {
    booked_ytd: number;
    open_ar: number;
    live_jobs: number;
    overdue_tasks: number;
    join_mapped: number;
    join_total: number;
  };
  jobs: AgencyJobRow[];
  needs_mapping: Array<{
    project_id: string;
    project_name: string;
    company_name: string;
    join: AgencyJoinStatus;
  }>;
  billed_without_project: Array<{
    customer_id: string;
    customer_name: string;
    billed_ytd: number;
    open_ar: number;
  }>;
  unlinked_invoices: AgencyInvoiceException[];
  resolution_options: AgencyResolutionOption[];
}
