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
  /** Missing scored evaluation criteria — never auto-added (a scoring
   * category name rarely lexically matches the requirement-phrased section
   * that covers it); surfaced here so the banner can say a human should
   * judge whether each one is genuinely uncovered. */
  ledgerScoredCriteriaAdvisoryCount?: number;
  ledgerScoredCriteriaAdvisoryTitles?: string[];
  /** Administrative/procedural submission constraints (source=
   * "submission_instruction") — deadlines, delivery/labelling instructions,
   * validity windows, copy counts, format rules. Never auto-added as a
   * section (nobody drafts a section titled "Proposal must be received no
   * later than August 3, 2026 by 3:00 P.M. (ET)") but never silently
   * dropped either — a real obligation the user must still see and comply
   * with, reported as its own compliance checklist distinct from the
   * drafting/attachment submission checklists below. */
  ledgerSubmissionInstructionsCount?: number;
  ledgerSubmissionInstructionsTitles?: string[];
  /** Set only when the blast-radius guard declined to apply otherwise-
   * eligible additions this pass (too many sections / too much growth for
   * one click) — surfaced so the banner says what it declined and why,
   * instead of silently applying nothing. */
  ledgerAdditionsDeclinedCount?: number;
  ledgerAdditionsDeclinedTitles?: string[];
  ledgerAdditionsDeclinedReason?: string | null;
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
  /** Narrative submission items (financial stability, awards, closing
   * statement, ...) the RFP's submission instructions call for that the
   * pipeline can still draft from the KB — kept strictly separate from
   * needsAttachment below. See buildScanRfpBanner's module note. */
  submissionNeedsDraftingCount?: number;
  submissionNeedsDraftingTitles?: string[];
  /** Signed/scanned PHYSICAL documents (W-9, Certificate of Insurance,
   * addenda acknowledgement, notarized affidavits, ...) a human must
   * obtain and attach — never satisfiable by a drafted section, however
   * complete that section's prose is. Reported independent of whatever the
   * compliance matrix happened to capture, and dropped once the draft
   * resolves the item (never re-flagged for something already handled). */
  submissionNeedsAttachmentCount?: number;
  submissionNeedsAttachmentTitles?: string[];
  /** Task 17: the Scan-RFP button used to never touch the budget at all —
   * "none" (no budget yet, not an error), "ok" (checked, clean), "repaired"
   * (deterministic fix — arithmetic, line-item classification, or prose
   * re-sync), "needs_human" / "repaired_needs_human" (a pricing JUDGEMENT
   * call — underbid vs the 00_Guide_Pricing floor, an RFP-forbidden travel
   * line, or a genuinely unrepairable invariant — reported, never a
   * fabricated dollar amount). Always surfaced explicitly so a clean budget
   * reads as "checked, no problems" rather than looking identical to "never
   * ran" — the same silent-pass ambiguity this project has already been
   * burned by twice. */
  disqualificationRiskCount?: number;
  disqualificationRisks?: string[];
  orchestratorLoopPasses?: number;
  budgetStatus?: "none" | "ok" | "repaired" | "needs_human" | "repaired_needs_human";
  budgetChanged?: boolean;
  budgetRegenerated?: boolean;
  budgetRepairedNotes?: string[];
  budgetEscalationNotes?: string[];
  /** Deterministic consistency fixes on the existing draft (primary contact,
   * duplicate refs, schedule compress, signed-cover DESIGNER NOTE). */
  consistencyFixesApplied?: number;
  consistencyFixSummaries?: string[];
  /** LLM manuscript-vs-RFP contradictions (not Go/No-Go capability gaps). */
  rfpContradictionCount?: number;
  rfpContradictionTitles?: string[];
  rfpContradictionRewrites?: number;
  rfpContradictionVerifyTags?: number;
  rfpContradictionUnresolved?: number;
  rfpContradictionSummary?: string;
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

  const dqCount = report.disqualificationRiskCount ?? report.disqualificationRisks?.length ?? 0;
  if (dqCount > 0) {
    clauses.push(
      `${dqCount} disqualification / gov-policy risk(s)${namedList(
        report.disqualificationRisks
      )}`
    );
  }

  const declined = report.ledgerAdditionsDeclinedCount ?? 0;
  if (declined > 0) {
    clauses.push(
      `declined to add ${declined} section(s) automatically${namedList(
        report.ledgerAdditionsDeclinedTitles
      )} (${report.ledgerAdditionsDeclinedReason ?? "over the safety guard"})`
    );
  }

  // Task 16: administrative submission constraints (deadlines, delivery
  // instructions, validity windows, ...) are real obligations, not
  // deliverables — reported as their own compliance checklist so a real
  // deadline never reads as just a declined/dropped addition.
  const submissionInstructions = report.ledgerSubmissionInstructionsCount ?? 0;
  if (submissionInstructions > 0) {
    clauses.push(
      `${submissionInstructions} submission requirement(s) to comply with${namedList(
        report.ledgerSubmissionInstructionsTitles
      )}`
    );
  }

  const scoredAdvisory = report.ledgerScoredCriteriaAdvisoryCount ?? 0;
  if (scoredAdvisory > 0) {
    clauses.push(
      `${scoredAdvisory} scored criteri${
        scoredAdvisory === 1 ? "on" : "a"
      } may not be covered${namedList(
        report.ledgerScoredCriteriaAdvisoryTitles
      )} — review manually`
    );
  }

  // Task 15: kept as two distinct clauses on purpose — "needs drafting" is a
  // narrative section the pipeline can still write from the KB; "needs an
  // attachment" is a hard submission blocker only a human can resolve
  // (upload/sign/scan). Collapsing these into one line is exactly the
  // failure mode this fix exists to prevent — a missing W-9 must never read
  // like "one more section to write."
  const needsAttachment = report.submissionNeedsAttachmentCount ?? 0;
  if (needsAttachment > 0) {
    clauses.push(
      `needs ${needsAttachment} physical document(s) attached before submission${namedList(
        report.submissionNeedsAttachmentTitles
      )} — signed/scanned files only, drafting cannot satisfy these`
    );
  }

  const needsDrafting = report.submissionNeedsDraftingCount ?? 0;
  if (needsDrafting > 0) {
    clauses.push(
      `${needsDrafting} narrative submission item(s) still need drafting${namedList(
        report.submissionNeedsDraftingTitles
      )}`
    );
  }

  const consistency = report.consistencyFixesApplied ?? 0;
  if (consistency > 0) {
    clauses.push(
      `applied ${consistency} consistency fix(es)${namedList(
        report.consistencyFixSummaries
      )}`
    );
  }

  const contradictions = report.rfpContradictionCount ?? 0;
  const rewrites = report.rfpContradictionRewrites ?? 0;
  const unresolved = report.rfpContradictionUnresolved ?? 0;
  if (rewrites > 0) {
    clauses.push(`fixed ${rewrites} manuscript-vs-RFP contradiction(s) by rewrite`);
  }
  if (unresolved > 0) {
    clauses.push(
      `${unresolved} contradiction(s) still need human input${namedList(
        report.rfpContradictionTitles
      )}`
    );
  } else if (contradictions > 0 && rewrites === 0) {
    clauses.push(
      `found ${contradictions} manuscript-vs-RFP contradiction(s)${namedList(
        report.rfpContradictionTitles
      )}`
    );
  }

  const merged = report.ledgerMergesApplied ?? 0;
  if (merged > 0) {
    clauses.push(`merged ${merged} duplicated requirement(s)`);
  }

  const cut = report.ledgerCutsApplied ?? 0;
  if (cut > 0) {
    clauses.push(
      `removed/trimmed ${cut} section(s) to stay within page limits and keep the proposal lean${namedList(
        report.ledgerCutsSectionTitles
      )}`
    );
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

  // Task 17: always say what happened to the budget, distinct from "not
  // checked" — a silent pass here reads identically to the scan never
  // touching the budget at all, which is the exact ambiguity that has
  // already burned this project twice.
  const budgetStatus = report.budgetStatus;
  const repairedNotes = (report.budgetRepairedNotes ?? []).join("; ");
  const escalationNotes = (report.budgetEscalationNotes ?? []).join("; ");
  if (report.budgetRegenerated) {
    clauses.push("regenerated the missing budget (Phase 3.5) and wrote it into the proposal");
  } else if (budgetStatus === "ok") {
    clauses.push("checked the budget thoroughly — no problems found");
  } else if (budgetStatus === "repaired") {
    clauses.push(
      `repaired the budget${repairedNotes ? ` (${repairedNotes})` : ""}`
    );
  } else if (budgetStatus === "needs_human") {
    clauses.push(
      `budget needs a human review before submission${
        escalationNotes ? ` (${escalationNotes})` : ""
      }`
    );
  } else if (budgetStatus === "repaired_needs_human") {
    clauses.push(
      `partially repaired the budget${repairedNotes ? ` (${repairedNotes})` : ""} ` +
        `but it still needs a human review before submission${
          escalationNotes ? ` (${escalationNotes})` : ""
        }`
    );
  }

  const verifyRemoved = report.verifyTagsRemoved ?? 0;
  const verifyKept = report.verifyTagsKept ?? 0;
  if (verifyRemoved > 0 || verifyKept > 0) {
    clauses.push(
      `VERIFY scrub removed ${verifyRemoved} optional tag(s)` +
        (verifyKept > 0 ? `, kept ${verifyKept} RFP-critical tag(s)` : "")
    );
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
