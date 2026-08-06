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

  it("says why required-section coverage could not be checked instead of reading as a silent no-op", () => {
    // Bug 1 regression: ledger_added/merged/cut reading 0 used to be
    // indistinguishable from "already compliant" even when the reconciler
    // never actually ran (no persisted or buildable requirement ledger). The
    // banner must say so instead of falling back to NOTHING_CHANGED_MESSAGE.
    const banner = buildScanRfpBanner({
      ledgerAdditionsApplied: 0,
      ledgerMergesApplied: 0,
      ledgerCutsApplied: 0,
      ledgerCheckSkippedReason:
        "no proposal execution plan persisted on this proposal's research cache",
    });
    expect(banner).toBe(
      "Could not check required-section coverage against the RFP " +
        "(no proposal execution plan persisted on this proposal's research cache)."
    );
  });

  it("reports missing scored criteria as advisory, never as an addition", () => {
    // Live-incident regression: a scored evaluation criterion like "Cost and
    // Overall Value" is a scoring category name, not a deliverable — it must
    // never show up as "Added ... section(s)"; it must show up as advisory.
    const banner = buildScanRfpBanner({
      ledgerScoredCriteriaAdvisoryCount: 5,
      ledgerScoredCriteriaAdvisoryTitles: [
        "Relevant Experience",
        "Strategic Approach and Methodology",
        "Personnel and Project Management",
        "Reporting and Performance Optimization",
        "Cost and Overall Value",
      ],
    });
    expect(banner).not.toContain("Added");
    expect(banner).toBe(
      '5 scored criteria may not be covered ("Relevant Experience", ' +
        '"Strategic Approach and Methodology", "Personnel and Project Management", ' +
        '"Reporting and Performance Optimization", "Cost and Overall Value") — ' +
        "review manually."
    );
  });

  it("reports declined additions from the blast-radius guard separately from applied ones", () => {
    const banner = buildScanRfpBanner({
      ledgerAdditionsApplied: 0,
      ledgerAdditionsDeclinedCount: 8,
      ledgerAdditionsDeclinedTitles: [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
      ],
      ledgerAdditionsDeclinedReason:
        "would add 8 section(s) to a 10-section proposal in one pass — over the blast-radius guard",
    });
    expect(banner).toBe(
      'Declined to add 8 section(s) automatically ("A", "B", "C", "D", "E", +3 more) ' +
        "(would add 8 section(s) to a 10-section proposal in one pass — over the blast-radius guard)."
    );
  });

  it("reports administrative submission instructions as a compliance checklist, never as an addition", () => {
    // Task 16 / live KVCC incident regression: "Proposal must be received no
    // later than August 3, 2026 by 3:00 P.M. (ET)" and its siblings are
    // administrative constraints you comply with, not deliverables — they
    // must never read as "Added ... section(s)" or even "declined to add",
    // and must never be silently dropped either.
    const banner = buildScanRfpBanner({
      ledgerAdditionsApplied: 1,
      ledgerAdditionsSectionTitles: ["Provide a detailed project schedule with milestones"],
      // 8 items — the exact count from the live KVCC banner (5 named + "+3 more").
      ledgerSubmissionInstructionsCount: 8,
      ledgerSubmissionInstructionsTitles: [
        "Proposal must be received no later than August 3, 2026 by 3:00 P.M. (ET)",
        "Proposal must be marked 'Marketing Plan' and submitted to specified address or email",
        "Include contractor's name(s)",
        "Include contact information (Address, phone, Fax, Email)",
        "Proposal must be valid for at least thirty (30) days after proposal due date",
        "Submit one original and three copies of the proposal",
        "Proposals must use 11-point font and 1-inch margins throughout",
        "The technical proposal shall not exceed 20 pages",
      ],
    });
    expect(banner).not.toContain("declined to add");
    expect(banner).toBe(
      'Added 1 missing required section(s) ' +
        '("Provide a detailed project schedule with milestones"); ' +
        '8 submission requirement(s) to comply with (' +
        '"Proposal must be received no later than August 3, 2026 by 3:00 P.M. (ET)", ' +
        '"Proposal must be marked \'Marketing Plan\' and submitted to specified address or email", ' +
        '"Include contractor\'s name(s)", "Include contact information (Address, phone, Fax, Email)", ' +
        '"Proposal must be valid for at least thirty (30) days after proposal due date", +3 more).'
    );
  });

  it("omits the submission-instructions clause entirely when the count is zero", () => {
    const banner = buildScanRfpBanner({
      verifyTagsRemoved: 2,
      ledgerSubmissionInstructionsCount: 0,
    });
    expect(banner).toBe("Removed 2 optional [VERIFY] tag(s).");
  });

  it("keeps a physical-document attachment distinct from a narrative section still needing drafting", () => {
    // Task 15: the report must never collapse "needs an attachment" (a hard
    // submission blocker only a human can resolve — signed W-9, Certificate
    // of Insurance) into "needs drafting" (a narrative section the pipeline
    // can still write). A drafted section about a W-9 is not a W-9.
    const banner = buildScanRfpBanner({
      submissionNeedsDraftingCount: 1,
      submissionNeedsDraftingTitles: [
        "Financial stability narrative (in proposal body)",
      ],
      submissionNeedsAttachmentCount: 2,
      submissionNeedsAttachmentTitles: ["IRS Form W-9", "Certificate(s) of Insurance"],
    });
    expect(banner).toBe(
      'Needs 2 physical document(s) attached before submission ' +
        '("IRS Form W-9", "Certificate(s) of Insurance") — signed/scanned files only, ' +
        "drafting cannot satisfy these; 1 narrative submission item(s) still need drafting " +
        '("Financial stability narrative (in proposal body)").'
    );
  });

  it("omits the attachment/drafting clauses entirely when both counts are zero", () => {
    const banner = buildScanRfpBanner({
      submissionNeedsDraftingCount: 0,
      submissionNeedsAttachmentCount: 0,
      verifyTagsRemoved: 2,
    });
    expect(banner).toBe("Removed 2 optional [VERIFY] tag(s).");
  });

  it("appends the skip-reason clause alongside other real changes", () => {
    const banner = buildScanRfpBanner({
      verifyTagsRemoved: 3,
      ledgerCheckSkippedReason: "no research cache persisted for this proposal",
    });
    expect(banner).toBe(
      "Removed 3 optional [VERIFY] tag(s); could not check required-section " +
        "coverage against the RFP (no research cache persisted for this proposal)."
    );
  });

  // Task 17 — the budget check must never read as a silent pass: "checked,
  // no problems" must look different from "never ran" (no clause at all).
  describe("budget outcome clause", () => {
    it("says the budget was checked with no problems when status is ok", () => {
      const banner = buildScanRfpBanner({ budgetStatus: "ok" });
      expect(banner).toBe("Checked the budget — no problems found.");
    });

    it("names what was repaired when status is repaired", () => {
      const banner = buildScanRfpBanner({
        budgetStatus: "repaired",
        budgetRepairedNotes: [
          "1 line item(s) recalculated (classification and/or arithmetic — e.g. a travel line no longer double-counted as an agency fee)",
        ],
      });
      expect(banner).toBe(
        "Repaired the budget (1 line item(s) recalculated (classification and/or " +
          "arithmetic — e.g. a travel line no longer double-counted as an agency fee))."
      );
    });

    it("surfaces the escalation reason when the budget needs a human", () => {
      const banner = buildScanRfpBanner({
        budgetStatus: "needs_human",
        budgetEscalationNotes: [
          "Proposed agency fees $3,500.00 are below 60% of the 00_Guide_Pricing floor $24,000.00",
        ],
      });
      expect(banner).toBe(
        "Budget needs a human review before submission (Proposed agency fees " +
          "$3,500.00 are below 60% of the 00_Guide_Pricing floor $24,000.00)."
      );
    });

    it("names both the repair and the remaining escalation for repaired_needs_human", () => {
      const banner = buildScanRfpBanner({
        budgetStatus: "repaired_needs_human",
        budgetRepairedNotes: ["agency fee subtotal corrected"],
        budgetEscalationNotes: ["travel/reimbursables priced in a remote-only engagement"],
      });
      expect(banner).toBe(
        "Partially repaired the budget (agency fee subtotal corrected) but it still needs " +
          "a human review before submission (travel/reimbursables priced in a remote-only engagement)."
      );
    });

    it("adds no clause at all when there is no budget yet", () => {
      const banner = buildScanRfpBanner({ budgetStatus: "none", verifyTagsRemoved: 0 });
      expect(banner).toBe(
        "No changes needed — the proposal already covers every mandatory requirement and is within the page limit."
      );
    });

    it("combines with unrelated clauses in the same style as other findings", () => {
      const banner = buildScanRfpBanner({
        verifyTagsRemoved: 2,
        budgetStatus: "repaired",
        budgetRepairedNotes: ["agency revenue estimate corrected ($3500 → $0)"],
      });
      expect(banner).toBe(
        "Removed 2 optional [VERIFY] tag(s); repaired the budget " +
          "(agency revenue estimate corrected ($3500 → $0))."
      );
    });
  });
});
