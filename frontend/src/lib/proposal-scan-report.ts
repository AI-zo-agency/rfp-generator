// Task 11: the Scan-RFP reconciler (ADD/MERGE/CUT) and the truncation/
// hallucination detectors already ran inside fulfillReport / review — this
// module is the only place that turns those numbers into what the user
// reads. Before this, ProposalDraftWorkspace.tsx only read the VERIFY-tag
// counts, so a scan that added 2 sections, merged 1, and trimmed 3 reported
// nothing about any of it.
//
// Pure and framework-free on purpose so it is unit-testable without
// mounting ProposalDraftWorkspace.

export interface ScanRfpFulfillReport {
  sectionsScanned?: number;
  verifyTagsRemoved?: number;
  verifyTagsKept?: number;
  ledgerAdditionsApplied?: number;
  ledgerAdditionsSectionTitles?: string[];
  ledgerMergesApplied?: number;
  ledgerMergesSectionTitles?: string[];
  ledgerCutsApplied?: number;
  ledgerCutsSectionTitles?: string[];
  truncationRepairedCount?: number;
  truncationRepairedSectionTitles?: string[];
  truncatedSectionsCount?: number;
  truncatedSectionTitles?: string[];
  unverifiedClaimsCount?: number;
  /** Set only when the ledger check never ran at all (no persisted or
   * buildable requirement ledger) — distinguishes "checked, nothing to fix"
   * from "never checked" so ledger_added/merged/cut reading 0 doesn't look
   * like a silent no-op. */
  ledgerCheckSkippedReason?: string | null;
}

const NOTHING_CHANGED_MESSAGE =
  "No changes needed — the proposal already covers every mandatory requirement and is within the page limit.";

const MAX_NAMED_TITLES = 5;

function namedList(titles: string[] | undefined): string {
  const names = (titles ?? []).filter(Boolean);
  if (names.length === 0) return "";
  const shown = names.slice(0, MAX_NAMED_TITLES);
  const quoted = shown.map((t) => `"${t}"`).join(", ");
  const extra = names.length - shown.length;
  return extra > 0 ? ` (${quoted}, +${extra} more)` : ` (${quoted})`;
}

/** Builds the plain-language "what happened" banner for the Scan RFP button. */
export function buildScanRfpBanner(report: ScanRfpFulfillReport): string {
  const clauses: string[] = [];

  const added = report.ledgerAdditionsApplied ?? 0;
  if (added > 0) {
    clauses.push(
      `Added ${added} missing required section(s)${namedList(
        report.ledgerAdditionsSectionTitles
      )}`
    );
  }

  const merged = report.ledgerMergesApplied ?? 0;
  if (merged > 0) {
    clauses.push(`merged ${merged} duplicated requirement(s)`);
  }

  const cut = report.ledgerCutsApplied ?? 0;
  if (cut > 0) {
    clauses.push(`trimmed ${cut} section(s) to the page limit`);
  }

  const removed = report.verifyTagsRemoved ?? 0;
  if (removed > 0) {
    clauses.push(`removed ${removed} optional [VERIFY] tag(s)`);
  }

  const kept = report.verifyTagsKept ?? 0;
  if (kept > 0) {
    clauses.push(
      `kept ${kept} [VERIFY] tag(s) only because the RFP critically requires them`
    );
  }

  const repaired = report.truncationRepairedCount ?? 0;
  if (repaired > 0) {
    clauses.push(
      `completed ${repaired} section(s) that were cut off mid-sentence${namedList(
        report.truncationRepairedSectionTitles
      )}`
    );
  }

  const truncated = report.truncatedSectionsCount ?? 0;
  if (truncated > 0) {
    clauses.push(
      `found ${truncated} section(s) with truncated content that need review${namedList(
        report.truncatedSectionTitles
      )}`
    );
  }

  const claims = report.unverifiedClaimsCount ?? 0;
  if (claims > 0) {
    clauses.push(`flagged ${claims} unverified/fabricated claim(s) for review`);
  }

  if (report.ledgerCheckSkippedReason) {
    clauses.push(
      `could not check required-section coverage against the RFP (${report.ledgerCheckSkippedReason})`
    );
  }

  if (clauses.length === 0) {
    return NOTHING_CHANGED_MESSAGE;
  }

  const [first, ...rest] = clauses;
  const capitalized = first.charAt(0).toUpperCase() + first.slice(1);
  return [capitalized, ...rest].join("; ") + ".";
}
