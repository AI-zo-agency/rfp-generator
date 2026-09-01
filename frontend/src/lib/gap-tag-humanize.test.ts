import { describe, expect, it } from "vitest";
import { gapChecklistLabel, humanizeGapTag, isInternalScanTag } from "./gap-tag-humanize";

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

  it("does not split the owner on a plain hyphen inside the body", () => {
    // "pre-proposal" has its own hyphen well before the real em dash later
    // in the sentence — the whole clause before that dash is too long to be
    // a real owner name, so it must fall back to the full, undivided body
    // rather than truncating mid-word ("...mandatory pre") like the old
    // hyphen-or-em-dash regex did.
    const h = humanizeGapTag(
      "[MANUAL FILL: Complete after the mandatory pre-proposal conference — record attendee name, sign-in date/time, and attach any required attendance proof.]"
    );
    expect(h.owner).toBeNull();
    expect(h.detail).toContain("mandatory pre-proposal conference");
    expect(h.detail).toContain("record attendee name");
    expect(h.detail.startsWith("MANUAL FILL:")).toBe(false);
  });

  it("keeps a short real owner name and splits on the em dash", () => {
    const h = humanizeGapTag(
      "[MANUAL FILL: Sonja — confirm insurance coverage amounts on the current COI]"
    );
    expect(h.owner).toBe("Sonja");
    expect(h.detail).toBe("confirm insurance coverage amounts on the current COI");
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

  it("does not dump deterministic.compliance keys into the label", () => {
    const h = humanizeGapTag(
      "[MANUAL FILL: Sonja — deterministic.compliance.budget_still_has_internal_pricing_flags_resolve_fee_decisions_with | BUDGET STILL HAS INTERNAL PRICING FLAGS — RESOLVE FEE DECISIONS WITH SONJA]"
    );
    expect(h.title).toBe("Budget needs review");
    expect(h.detail.toLowerCase()).not.toContain("deterministic");
    expect(h.detail.toLowerCase()).toContain("pricing flags");
  });

  it("hides fabricated deferred-information scan codes", () => {
    const tag =
      "[MANUAL FILL: Sonja — deterministic.fabricated_fact.deferred_information_upon_request_is_forbidden.provide_full_detail | DEFERRED INFORMATION ('UPON REQUEST' IS FORBIDDEN - PROVIDE FULL DETAIL)]";
    expect(isInternalScanTag(tag)).toBe(true);
    const h = humanizeGapTag(tag);
    expect(h.detail.toLowerCase()).not.toContain("deterministic");
    expect(h.title).toBe("Do not defer facts");
  });
});
