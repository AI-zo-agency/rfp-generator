import type { OutlineSection } from "@/types/proposal";

export interface SectionContentChange {
  id: string;
  title: string;
  charsBefore: number;
  charsAfter: number;
}

export interface ProposalSnapshotDiff {
  added: OutlineSection[];
  removed: OutlineSection[];
  modified: SectionContentChange[];
  unchangedCount: number;
}

function normContent(s: string | undefined): string {
  return (s ?? "").replace(/\r\n/g, "\n").trim();
}

export function diffProposalSections(
  before: OutlineSection[],
  after: OutlineSection[]
): ProposalSnapshotDiff {
  const beforeById = new Map(before.map((s) => [s.id, s]));
  const afterById = new Map(after.map((s) => [s.id, s]));

  const added: OutlineSection[] = [];
  const removed: OutlineSection[] = [];
  const modified: SectionContentChange[] = [];
  let unchangedCount = 0;

  for (const section of after) {
    const prev = beforeById.get(section.id);
    if (!prev) {
      if (normContent(section.content)) {
        added.push(section);
      }
      continue;
    }
    const b = normContent(prev.content);
    const a = normContent(section.content);
    if (b === a) {
      unchangedCount += 1;
    } else {
      modified.push({
        id: section.id,
        title: section.title,
        charsBefore: b.length,
        charsAfter: a.length,
      });
    }
  }

  for (const section of before) {
    if (!afterById.has(section.id) && normContent(section.content)) {
      removed.push(section);
    }
  }

  return { added, removed, modified, unchangedCount };
}

export interface FulfillScanSummary {
  closingAddedSections?: Array<{ id: string; title: string }>;
  closingDetectedSections?: Array<{ id: string; title: string }>;
  closingAlreadyPresent?: Array<{ id: string; title: string }>;
  submissionDeliverablesAdded?: Array<{ id: string; title: string; kind?: string }>;
  budgetScan?: string[];
  kpiScan?: string[];
  budgetKpiSummary?: string[];
  inPlaceFixCount?: number;
  humanDecisionGaps?: string[];
  logs?: string[];
  /** Stage 1 — number of sections trimmed by the whole-manuscript repetition pass. */
  repetitionSweep?: number;
  /** Stage 18 — the review agent (three acts + four detectors). */
  qualityGate?: {
    ran?: boolean;
    roundsRun?: number;
    stoppedReason?: string;
    changes?: string[];
    convergence?: string[];
    tickets?: Array<{ outcome?: string; detector?: string }>;
    claims?: Array<{ status?: string }>;
  };
  /** Stage 20 — submission readiness. */
  readiness?: {
    score?: number;
    measured?: boolean;
    confidence?: string;
    confidenceNote?: string;
    verdict?: string;
    openDisqualifying?: number;
    openScored?: number;
  };
}

/**
 * What the review agent actually did, in the order a reader cares about.
 *
 * Reports "did not run" explicitly rather than rendering nothing: a stage that silently
 * produces no lines is indistinguishable from a clean draft, and the whole point of
 * this pass is knowing what was checked.
 */
export function formatQualityGateLines(
  gate: FulfillScanSummary["qualityGate"]
): string[] {
  if (!gate) return [];
  if (gate.ran === false) {
    return [`Review agent: did not run${gate.stoppedReason ? ` — ${gate.stoppedReason}` : ""}`];
  }

  const tickets = gate.tickets ?? [];
  const count = (outcome: string) =>
    tickets.filter((t) => t.outcome === outcome).length;
  const fixed = count("fixed");
  const manual = count("manual_fill");
  const reverted = count("reverted") + count("unfixed");

  const parts: string[] = [];
  if (fixed) parts.push(`${fixed} fixed`);
  if (manual) parts.push(`${manual} sent to MANUAL FILL`);
  if (reverted) parts.push(`${reverted} left open`);

  const lines: string[] = [
    `Review agent: ${parts.length ? parts.join(", ") : "no issues found"}` +
      (gate.roundsRun ? ` (${gate.roundsRun} round${gate.roundsRun === 1 ? "" : "s"})` : ""),
  ];

  const unresolved = (gate.claims ?? []).filter((c) => c.status === "unresolved").length;
  const corrected = (gate.claims ?? []).filter((c) => c.status === "contradicted").length;
  if (corrected) lines.push(`Facts corrected from the knowledge base: ${corrected}`);
  if (unresolved) lines.push(`Claims kept but unverified — confirm before submitting: ${unresolved}`);

  // A per-detector breakdown, so "what improved" is legible rather than a total.
  const byDetector = new Map<string, number>();
  for (const t of tickets) {
    if (t.outcome !== "fixed" || !t.detector) continue;
    byDetector.set(t.detector, (byDetector.get(t.detector) ?? 0) + 1);
  }
  const labels: Record<string, string> = {
    slop: "filler removed",
    repetition: "repetition cut",
    consistency: "contradictions resolved",
    evaluator: "scored-criteria gaps closed",
  };
  for (const [detector, n] of byDetector) {
    lines.push(`  ${labels[detector] ?? detector}: ${n}`);
  }

  if (gate.stoppedReason) lines.push(`Stopped because ${gate.stoppedReason}`);
  return lines;
}

/** The submission verdict — the line worth reading first. */
export function formatReadinessLines(
  readiness: FulfillScanSummary["readiness"]
): string[] {
  if (!readiness) return [];
  const lines: string[] = [];
  const score =
    readiness.measured === false ? "not measured" : `${readiness.score ?? 0}%`;
  lines.push(`Readiness: ${score}${readiness.verdict ? ` — ${readiness.verdict}` : ""}`);
  if (readiness.confidence) {
    lines.push(
      `Confidence: ${readiness.confidence}${
        readiness.confidenceNote ? ` (${readiness.confidenceNote})` : ""
      }`
    );
  }
  if (readiness.openDisqualifying) {
    lines.push(`Blockers open: ${readiness.openDisqualifying}`);
  }
  return lines;
}

export function formatScanSummaryLines(summary: FulfillScanSummary | undefined): string[] {
  if (!summary) return [];
  const lines: string[] = [];
  const added = summary.closingAddedSections ?? [];
  if (added.length) {
    lines.push(`Sections added: ${added.map((s) => s.title).join(", ")}`);
  }
  const deliverables = summary.submissionDeliverablesAdded ?? [];
  if (deliverables.length) {
    lines.push(
      `RFP deliverables drafted: ${deliverables.map((d) => d.title).join(", ")}`
    );
  }
  if (summary.inPlaceFixCount) {
    lines.push(`In-place fixes: ${summary.inPlaceFixCount}`);
  }
  if (summary.repetitionSweep) {
    lines.push(`Repetition sweep: ${summary.repetitionSweep} section(s) trimmed`);
  }
  lines.push(...formatQualityGateLines(summary.qualityGate));
  lines.push(...formatReadinessLines(summary.readiness));
  for (const block of [summary.budgetScan, summary.kpiScan]) {
    if (block?.length) {
      lines.push(...block.slice(0, 4));
    }
  }
  return lines;
}
