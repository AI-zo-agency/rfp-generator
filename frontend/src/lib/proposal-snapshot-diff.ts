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
  for (const block of [summary.budgetScan, summary.kpiScan]) {
    if (block?.length) {
      lines.push(...block.slice(0, 4));
    }
  }
  return lines;
}
