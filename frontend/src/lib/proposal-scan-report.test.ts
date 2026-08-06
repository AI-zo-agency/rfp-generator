import { describe, expect, it } from "vitest";
import { buildScanRfpBanner } from "./proposal-scan-report";

describe("buildScanRfpBanner", () => {
  it("names what changed when the reconciler added, merged, trimmed and scrubbed", () => {
    const banner = buildScanRfpBanner({
      sectionsScanned: 3,
      verifyTagsRemoved: 14,
      verifyTagsKept: 1,
      ledgerAdditionsApplied: 2,
      ledgerAdditionsSectionTitles: ["A signed cover letter", "Certificate of insurance"],
      ledgerMergesApplied: 1,
      ledgerCutsApplied: 3,
    });
    expect(banner).toBe(
      'Added 2 missing required section(s) ("A signed cover letter", "Certificate of insurance"); ' +
        "merged 1 duplicated requirement(s); trimmed 3 section(s) to the page limit; " +
        "removed 14 optional [VERIFY] tag(s); " +
        "kept 1 [VERIFY] tag(s) only because the RFP critically requires them."
    );
  });

  it("says explicitly that nothing changed when every count is zero", () => {
    const banner = buildScanRfpBanner({
      sectionsScanned: 0,
      verifyTagsRemoved: 0,
      verifyTagsKept: 0,
      ledgerAdditionsApplied: 0,
      ledgerMergesApplied: 0,
      ledgerCutsApplied: 0,
      truncatedSectionsCount: 0,
      unverifiedClaimsCount: 0,
    });
    expect(banner).toBe(
      "No changes needed — the proposal already covers every mandatory requirement and is within the page limit."
    );
  });

  it("surfaces truncated sections by name and the unverified-claims count", () => {
    const banner = buildScanRfpBanner({
      sectionsScanned: 0,
      verifyTagsRemoved: 0,
      verifyTagsKept: 0,
      truncatedSectionsCount: 9,
      truncatedSectionTitles: [
        "rfp-sec-10",
        "rfp-sec-8",
        "Gil Aranowitz — Bio",
        "Harsh Mohite — Bio",
        "Justin Bronson — Bio",
        "Sonja Anderson — Bio",
        "Vivek Patel — Bio",
        "Case Study — Deschutes Brewery",
        "Case Study — City of Umatilla",
      ],
      unverifiedClaimsCount: 9,
    });
    expect(banner).toBe(
      'Found 9 section(s) with truncated content that need review (' +
        '"rfp-sec-10", "rfp-sec-8", "Gil Aranowitz — Bio", "Harsh Mohite — Bio", "Justin Bronson — Bio", +4 more); ' +
        "flagged 9 unverified/fabricated claim(s) for review."
    );
  });

  it("reports repaired truncation separately from what still needs review", () => {
    const banner = buildScanRfpBanner({
      truncationRepairedCount: 2,
      truncationRepairedSectionTitles: [
        "Gil Aranowitz — Bio",
        "Case Study — Deschutes Brewery",
      ],
      truncatedSectionsCount: 1,
      truncatedSectionTitles: ["Case Study — City of Umatilla"],
    });
    expect(banner).toBe(
      'Completed 2 section(s) that were cut off mid-sentence (' +
        '"Gil Aranowitz — Bio", "Case Study — Deschutes Brewery"); ' +
        'found 1 section(s) with truncated content that need review ' +
        '("Case Study — City of Umatilla").'
    );
  });

  it("does not report a merged-away (cross-referenced) section as truncated", () => {
    // Regression: the reconciler's MERGE cross-reference note used to end in
    // a bare markdown-italics "_", which the backend T1 truncation gate
    // misread as a mid-sentence cutoff — every section a MERGE
    // cross-referenced was reported here as needing review even though nothing
    // was cut off. This banner-level test locks in the observable contract:
    // a merge with no truncation findings must never produce a truncation
    // clause naming the merged-away section.
    const banner = buildScanRfpBanner({
      ledgerMergesApplied: 1,
      ledgerMergesSectionTitles: ["Section 1.5"],
      truncationRepairedCount: 2,
      truncationRepairedSectionTitles: [
        "Gil Aranowitz — Bio",
        "Case Study — Deschutes Brewery",
      ],
      truncatedSectionsCount: 1,
      truncatedSectionTitles: ["Case Study — City of Umatilla"],
    });
    expect(banner).not.toContain("Attachments Checklist");
    expect(banner).not.toContain("Contract Acknowledgment");
    expect(banner).toBe(
      "Merged 1 duplicated requirement(s); " +
        'completed 2 section(s) that were cut off mid-sentence (' +
        '"Gil Aranowitz — Bio", "Case Study — Deschutes Brewery"); ' +
        'found 1 section(s) with truncated content that need review ' +
        '("Case Study — City of Umatilla").'
    );
  });

  it("omits a clause entirely when its count is zero instead of printing 0", () => {
    const banner = buildScanRfpBanner({
      verifyTagsRemoved: 5,
      verifyTagsKept: 0,
      ledgerAdditionsApplied: 0,
    });
    expect(banner).toBe("Removed 5 optional [VERIFY] tag(s).");
  });
});
