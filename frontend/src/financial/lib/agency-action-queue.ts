import type { AgencyInvoiceException, AgencyJobRow, AgencyOverview } from "../types/agency";

const PRIORITY = { delivery: 0, mapping: 1, receivable: 2, invoice: 3 } as const;

interface AgencyActionBase {
  id: string;
  priority: (typeof PRIORITY)[keyof typeof PRIORITY];
  title: string;
  detail: string;
  amount: number;
}

export interface AgencyDeliveryAction extends AgencyActionBase {
  kind: "delivery";
  priority: typeof PRIORITY.delivery;
  projectId: string;
  source: AgencyJobRow;
}

export interface AgencyMappingAction extends AgencyActionBase {
  kind: "mapping";
  priority: typeof PRIORITY.mapping;
  projectId: string;
  source: AgencyJobRow;
}

export interface AgencyReceivableAction extends AgencyActionBase {
  kind: "receivable";
  priority: typeof PRIORITY.receivable;
  projectId: string;
  source: AgencyJobRow;
}

export interface AgencyInvoiceAction extends AgencyActionBase {
  kind: "invoice";
  priority: typeof PRIORITY.invoice;
  invoiceId: string;
  source: AgencyInvoiceException;
}

export type AgencyAction =
  | AgencyDeliveryAction
  | AgencyMappingAction
  | AgencyReceivableAction
  | AgencyInvoiceAction;

function money(value: number | null | undefined): number {
  return Number.isFinite(value) ? Number(value) : 0;
}

function jobName(job: AgencyJobRow): string {
  return job.project_name || job.job_label || job.company_name || "Untitled project";
}

function receivableKey(job: AgencyJobRow): string {
  return job.client_map_id || job.client_name || job.company_name || job.project_id;
}

function isDeliveryRisk(job: AgencyJobRow): boolean {
  return job.status.toLowerCase() === "late" || job.health.toLowerCase() === "bad";
}

function needsMapping(job: AgencyJobRow): boolean {
  return ["needs mapping", "ambiguous", "suggested"].includes(job.join);
}

function compareActions(a: AgencyAction, b: AgencyAction): number {
  return a.priority - b.priority || b.amount - a.amount || a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
}

/** Derives a deterministic, owner-facing queue without fetching or mutating API data. */
export function buildAgencyActions(data: AgencyOverview): AgencyAction[] {
  const actions: AgencyAction[] = [];
  const receivables = new Map<string, AgencyJobRow>();

  for (const job of data.jobs) {
    const amount = money(job.open_ar);
    const name = jobName(job);

    if (isDeliveryRisk(job)) {
      const reason = job.status.toLowerCase() === "late" ? "Late" : "At-risk";
      actions.push({
        id: `delivery:${job.project_id}`,
        kind: "delivery",
        priority: PRIORITY.delivery,
        title: `${reason}: ${name}`,
        detail: job.company_name || job.client_name || job.job_label,
        amount,
        projectId: job.project_id,
        source: job,
      });
    }

    if (needsMapping(job)) {
      actions.push({
        id: `mapping:${job.project_id}`,
        kind: "mapping",
        priority: PRIORITY.mapping,
        title: `Map: ${name}`,
        detail: job.company_name || job.client_name || job.join,
        amount,
        projectId: job.project_id,
        source: job,
      });
    }

    if (amount > 0) {
      const key = receivableKey(job);
      const current = receivables.get(key);
      if (!current || money(current.open_ar) < amount) receivables.set(key, job);
    }
  }

  for (const job of receivables.values()) {
    const amount = money(job.open_ar);
    actions.push({
      id: `receivable:${receivableKey(job)}`,
      kind: "receivable",
      priority: PRIORITY.receivable,
      title: `Collect: ${job.client_name || job.company_name || jobName(job)}`,
      detail: jobName(job),
      amount,
      projectId: job.project_id,
      source: job,
    });
  }

  for (const invoice of data.unlinked_invoices) {
    const amount = money(invoice.open_ar) || money(invoice.total_amt);
    const customer = invoice.customer_name || invoice.customer_id || "Unknown customer";
    const label = invoice.invoice_number || invoice.invoice_id;
    actions.push({
      id: `invoice:${invoice.invoice_id}`,
      kind: "invoice",
      priority: PRIORITY.invoice,
      title: `Reconcile invoice ${label}`,
      detail: customer,
      amount,
      invoiceId: invoice.invoice_id,
      source: invoice,
    });
  }

  return actions.sort(compareActions);
}
