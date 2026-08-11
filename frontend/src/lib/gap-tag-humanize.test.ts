import { describe, expect, it } from "vitest";
import { gapChecklistLabel, humanizeGapTag } from "./gap-tag-humanize";

describe("humanizeGapTag", () => {
  it("turns KPI lock tags into plain asks", () => {
    const h = humanizeGapTag(
      "[MANUAL FILL: Sonja — deterministic.manuscript_locks.rfq_named_kpi_missing_from_x | RFQ-named KPI missing from Methodology/Reporting: '# of print and digital materials']"
    );
    expect(h.title).toBe("Add RFP KPIs to reporting");
    expect(h.detail).toContain("print and digital");
    expect(h.owner).toBe("Sonja");
  });

  it("humanizes VERIFY tags", () => {
    const h = humanizeGapTag("[VERIFY: Client testimonial from Deschutes County contact]");
    expect(h.title).toBe("Confirm before submit");
    expect(h.detail).toContain("Deschutes");
  });
});

describe("gapChecklistLabel", () => {
  it("does not show deterministic codes", () => {
    const label = gapChecklistLabel(
      "[MANUAL FILL: Sonja — deterministic.manuscript_locks.rfq_named_kpi_x | RFQ-named KPI missing: '# of PM meetings']"
    );
    expect(label).not.toContain("deterministic");
    expect(label).toContain("Sonja");
    expect(label).toContain("KPI");
  });
});
