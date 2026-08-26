import { describe, expect, it } from "vitest";
import {
  applyAgencyLoadResult,
  filterAgencyActions,
  filterAgencyGroups,
  formatAgencyFreshness,
  getAgencyLoadError,
  getAgencyEmptyMessage,
  getInvoiceResolutionOutcome,
  invoiceStatusPresentation,
  invoiceRelationshipLabel,
} from "./AgencyJobsDemo";

const groups = [
  { id: "late", status: "Late", join: "confirmed", jobCount: 1, openAr: 0, billedYtd: 0, jobs: [{ status: "Late", health: "good", join: "confirmed" }] },
  { id: "map", status: "On track", join: "needs mapping", jobCount: 1, openAr: 0, billedYtd: 0, jobs: [{ status: "On track", health: "good", join: "needs mapping" }] },
  { id: "ar", status: "On track", join: "confirmed", jobCount: 1, openAr: 500, billedYtd: 0, jobs: [{ status: "On track", health: "good", join: "confirmed" }] },
] as Parameters<typeof filterAgencyGroups>[0];

describe("AgencyJobsDemo control-room helpers", () => {
  it("filters grouped client portfolios without changing their project money", () => {
    expect(filterAgencyGroups(groups, "attention").map((group) => group.id)).toEqual(["late"]);
    expect(filterAgencyGroups(groups, "mapping").map((group) => group.id)).toEqual(["map"]);
    expect(filterAgencyGroups(groups, "financial")[0]?.openAr).toBe(500);
  });

  it("formats source freshness for the compact snapshot", () => {
    expect(formatAgencyFreshness("2026-08-26T09:15:00Z")).toContain("Updated");
    expect(formatAgencyFreshness(null)).toBe("Freshness unavailable");
  });

  it("keeps cached content visible when a refresh fails", () => {
    expect(getAgencyLoadError("Network unavailable", true)).toContain("showing the last loaded data");
    expect(getAgencyLoadError("Network unavailable", false)).toBe("Could not load agency overview: Network unavailable");
  });

  it("retains the loaded overview when a later refresh fails", () => {
    const prior = { year: 2026 } as Parameters<typeof applyAgencyLoadResult>[0];

    expect(applyAgencyLoadResult(prior, { ok: false, message: "Network unavailable" })).toEqual({
      data: prior,
      error: "Refresh failed: Network unavailable. We’re showing the last loaded data; retry to refresh it.",
    });
  });

  it("filters the independent owner queue by action kind", () => {
    const actions = [
      { kind: "delivery" },
      { kind: "mapping" },
      { kind: "receivable" },
      { kind: "invoice" },
    ] as Parameters<typeof filterAgencyActions>[0];

    expect(filterAgencyActions(actions, "ar")).toEqual([actions[2]]);
    expect(filterAgencyActions(actions, "invoices")).toEqual([actions[3]]);
    expect(filterAgencyActions(actions, "all")).toHaveLength(4);
  });

  it("keeps overdue invoices out of the green paid presentation", () => {
    expect(invoiceStatusPresentation({ status: "overdue", open_ar: 100 })).toEqual({ label: "overdue", tone: "warn" });
    expect(invoiceStatusPresentation({ status: "paid", open_ar: 0 })).toEqual({ label: "paid", tone: "good" });
    expect(invoiceStatusPresentation({ status: "voided", open_ar: 0 })).toEqual({ label: "voided", tone: "muted" });
  });

  it("uses the explicit missing-relationship label in the watchlist", () => {
    expect(invoiceRelationshipLabel({ status: "paid" } as Parameters<typeof invoiceRelationshipLabel>[0])).toBe("No project or job linked");
  });

  it("distinguishes an empty queue from an unavailable overview", () => {
    expect(getAgencyEmptyMessage(true)).toBe("No owner actions match this filter.");
    expect(getAgencyEmptyMessage(false)).toContain("Retry to load");
  });

  it("reports a saved invoice when the subsequent overview refresh fails", () => {
    expect(getInvoiceResolutionOutcome(false)).toBe("Invoice resolution was saved, but the overview needs a refresh. Retry to confirm the latest data.");
    expect(getInvoiceResolutionOutcome(true)).toBeNull();
  });
});

// The Node test environment cannot dispatch browser events. Verify the drawer's
// open → note → save → reopen path, focus trap, and mobile/desktop viewports in Playwright.
