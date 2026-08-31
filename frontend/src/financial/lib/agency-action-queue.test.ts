import { describe, expect, it } from "vitest";
import { buildAgencyActions } from "./agency-action-queue";
import type { AgencyJobRow, AgencyOverview } from "../types/agency";

function job(patch: Partial<AgencyJobRow> = {}): AgencyJobRow {
  return {
    project_id: "project-1",
    job_label: "ACME 1001",
    project_name: "Website refresh",
    company_name: "Acme Corp",
    client_name: "Acme Corp",
    status: "current",
    health: "healthy",
    hours_mtd_minutes: 0,
    billed_ytd: 1000,
    open_ar: 250,
    join: "confirmed",
    client_map_id: "acme",
    link_confidence: "confirmed",
    via: "tag",
    ...patch,
  };
}

function overview(patch: Partial<AgencyOverview> = {}): AgencyOverview {
  return {
    year: 2026,
    position: {
      booked_ytd: 0,
      open_ar: 0,
      live_jobs: 0,
      overdue_tasks: 0,
      join_mapped: 0,
      join_total: 0,
    },
    jobs: [],
    needs_mapping: [],
    billed_without_project: [],
    unlinked_invoices: [],
    resolution_options: [],
    ...patch,
  };
}

describe("buildAgencyActions", () => {
  it("orders late delivery before mapping, AR, and invoice reconciliation", () => {
    const actions = buildAgencyActions(overview({
      jobs: [
        job({ project_id: "late", status: "late" }),
        job({ project_id: "map", join: "needs mapping" }),
      ],
      unlinked_invoices: [{
        invoice_id: "inv-1",
        invoice_number: "1001",
        customer_id: "acme",
        customer_name: "Acme Corp",
        txn_date: "2026-01-01",
        due_date: "2026-02-01",
        total_amt: 500,
        open_ar: 500,
      }],
    }));

    expect(actions.map((action) => action.kind)).toEqual([
      "delivery", "mapping", "receivable", "invoice",
    ]);
  });

  it("does not create a receivable action for a zero-AR client", () => {
    const actions = buildAgencyActions(overview({ jobs: [job({ open_ar: 0 })] }));

    expect(actions.some((action) => action.kind === "receivable")).toBe(false);
  });

  it("deduplicates repeated client receivables without adding job money", () => {
    const actions = buildAgencyActions(overview({
      jobs: [
        job({ project_id: "one", open_ar: 800 }),
        job({ project_id: "two", open_ar: 800 }),
      ],
    }));

    const receivables = actions.filter((action) => action.kind === "receivable");
    expect(receivables).toHaveLength(1);
    expect(receivables[0]?.amount).toBe(800);
  });

  it("uses the largest open AR for repeated client receivables", () => {
    const actions = buildAgencyActions(overview({
      jobs: [
        job({ project_id: "smaller", open_ar: 350 }),
        job({ project_id: "larger", open_ar: 800 }),
      ],
    }));

    const receivables = actions.filter((action) => action.kind === "receivable");
    expect(receivables).toHaveLength(1);
    expect(receivables[0]).toMatchObject({ projectId: "larger", amount: 800 });
  });

  it("keeps malformed monetary values out of action amounts", () => {
    const invalidMoney = Number.NaN as unknown as number;
    const actions = buildAgencyActions(overview({
      jobs: [job({ project_id: "invalid-job", open_ar: invalidMoney })],
      unlinked_invoices: [{
        invoice_id: "invalid-invoice",
        invoice_number: "1002",
        customer_id: "acme",
        customer_name: "Acme Corp",
        txn_date: "2026-01-01",
        due_date: "2026-02-01",
        total_amt: invalidMoney,
        open_ar: invalidMoney,
      }],
    }));

    expect(actions.every((action) => Number.isFinite(action.amount))).toBe(true);
  });

  it("uses action id to break otherwise equal ordering ties", () => {
    const actions = buildAgencyActions(overview({
      jobs: [
        job({ project_id: "z-project", join: "needs mapping", open_ar: 100 }),
        job({ project_id: "a-project", join: "needs mapping", open_ar: 100 }),
      ],
    }));

    expect(actions.filter((action) => action.kind === "mapping").map((action) => action.id)).toEqual([
      "mapping:a-project", "mapping:z-project",
    ]);
  });
});
