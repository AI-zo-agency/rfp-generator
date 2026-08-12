/**
 * Classify Go-RFP proposal draft progress for the Switch RFP drawer filters.
 */

export type ProposalDraftProgressFilter =
  | "all"
  | "not_started"
  | "in_progress"
  | "done";

export type ProposalDraftSummary = {
  rfpId: string;
  filledCount: number;
  sectionCount: number;
  updatedAt: string;
};

export type ProposalDraftProgressStatus =
  | "not_started"
  | "in_progress"
  | "done";

export function classifyProposalDraftProgress(
  summary: ProposalDraftSummary | null | undefined
): ProposalDraftProgressStatus {
  if (!summary) return "not_started";
  const filled = Math.max(0, Number(summary.filledCount) || 0);
  const total = Math.max(0, Number(summary.sectionCount) || 0);
  if (filled <= 0) return "not_started";
  if (total > 0 && filled >= total) return "done";
  return "in_progress";
}

export const PROPOSAL_DRAFT_PROGRESS_LABELS: Record<
  ProposalDraftProgressStatus,
  string
> = {
  not_started: "Not started",
  in_progress: "In progress",
  done: "Done",
};

export const PROPOSAL_DRAFT_PROGRESS_FILTERS: {
  id: ProposalDraftProgressFilter;
  label: string;
}[] = [
  { id: "all", label: "All" },
  { id: "not_started", label: "Not started" },
  { id: "in_progress", label: "In progress" },
  { id: "done", label: "Done" },
];
