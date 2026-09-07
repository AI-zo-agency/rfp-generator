import type { TimesheetEntry } from "../components/IWorkerTimesheetsTable";

export type PeriodGranularity = "week" | "month";

export interface PeriodWindow {
  start: string;
  end: string;
  label: string;
}

export interface PeriodSelected extends PeriodWindow {
  is_current: boolean;
}

export interface PeriodMetrics {
  hours: number;
  spend_usd: number;
  scope_risk_usd: number;
  entries_count: number;
  active_contractors: number;
}

export interface PeriodDelta {
  hours_pct: number | null;
  spend_pct: number | null;
  scope_risk_pct: number | null;
}

export interface PeriodContractor {
  name: string;
  rate: number;
  hours: number;
  spend_usd: number;
  scope_risk_usd: number;
  expected_hours: number;
  utilization_pct: number | null;
  hours_delta_pct: number | null;
  spend_delta_pct: number | null;
}

export type PeriodSignalSeverity = "cost" | "scope" | "capacity";

export interface PeriodSignal {
  id: string;
  severity: PeriodSignalSeverity;
  headline: string;
  detail: string;
  contractor: string | null;
}

export interface PeriodHistoryPoint {
  granularity: PeriodGranularity;
  start: string;
  hours: number;
  spend_usd: number;
  scope_risk_usd: number;
}

export interface PeriodWeeklyInMonthContractor {
  name: string;
  hours: number;
}

export interface PeriodWeeklyInMonth {
  start: string;
  end: string;
  label: string;
  contractors: PeriodWeeklyInMonthContractor[];
  total_hours: number;
}

export type TeamworkReconcileRowStatus =
  | "match"
  | "mismatch"
  | "iworker_only"
  | "no_teamwork_match";

export interface TeamworkReconcileProject {
  name: string;
  hours: number;
}

export interface TeamworkReconcileRow {
  contractor: string;
  iworker_hours: number;
  teamwork_hours: number | null;
  delta_hours: number | null;
  status: TeamworkReconcileRowStatus;
  teamwork_user_id: string | null;
  teamwork_name: string | null;
  teamwork_projects: TeamworkReconcileProject[];
}

export interface TeamworkReconciliation {
  status: "ok" | "unavailable" | "error";
  detail?: string;
  period_start: string;
  period_end: string;
  tolerance_hours: number;
  rows: TeamworkReconcileRow[];
  summary: {
    matched: number;
    mismatched: number;
    iworker_only: number;
    no_teamwork_match: number;
  };
}

export interface PeriodInsights {
  timezone: string;
  granularity: PeriodGranularity;
  selected: PeriodSelected;
  previous: PeriodWindow;
  current: PeriodMetrics;
  previous_metrics: PeriodMetrics;
  delta: PeriodDelta;
  contractors: PeriodContractor[];
  signals: PeriodSignal[];
  available_periods: PeriodWindow[];
  expected_hours: number;
  weekly_in_month: PeriodWeeklyInMonth[];
  teamwork_reconciliation?: TeamworkReconciliation;
}

export interface IWorkerTabMeta {
  name: string;
  rate: number;
  total_hours: number;
  total_spend: number;
  active_entries: number;
}

export interface IWorkerSummary {
  total_logged_hours: number;
  total_spend_usd: number;
  active_tasks_count: number;
  hourly_rate_usd: number;
}

export interface IWorkerTimesheetsMeta {
  unparsed_date_count: number;
  snapshot_upserted: boolean;
  spreadsheet_id: string;
}

export interface IWorkerTimesheetsResponse {
  contractor: string;
  source: string;
  status: string;
  is_live_oauth_sync: boolean;
  tabs: IWorkerTabMeta[];
  summary: IWorkerSummary;
  timesheets: TimesheetEntry[];
  period_insights: PeriodInsights;
  period_history: PeriodHistoryPoint[];
  meta: IWorkerTimesheetsMeta;
}
