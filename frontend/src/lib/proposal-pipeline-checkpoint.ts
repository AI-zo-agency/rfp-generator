import type { ProposalOutline, ProposalResearch } from "@/types/proposal";
import { staticSections1to3Complete } from "@/lib/proposal-draft";

export type PipelinePhase =
  | "sections-1-3"
  | "phase-2"
  | "phase-3"
  | "phase-3-6-self-edit"
  | "phase-3-5-budget"
  | "phase-4-review"
  | "complete";

export const PIPELINE_PHASE_ORDER: PipelinePhase[] = [
  "sections-1-3",
  "phase-2",
  "phase-3",
  "phase-3-6-self-edit",
  "phase-3-5-budget",
  "phase-4-review",
];

export const PIPELINE_PHASE_LABELS: Record<PipelinePhase, string> = {
  "sections-1-3": "Sections 1–3",
  "phase-2": "Phase 2 research",
  "phase-3": "Phase 3 drafting",
  "phase-3-6-self-edit": "Senior editor polish",
  "phase-3-5-budget": "Budget build",
  "phase-4-review": "Pre-submit review",
  complete: "Complete",
};

export interface ProposalPipelineCheckpoint {
  lastCompletedPhase?: PipelinePhase | null;
  inProgressPhase?: PipelinePhase | null;
  lastFailedPhase?: PipelinePhase | null;
  lastError?: string | null;
  resumeFromPhase?: PipelinePhase | null;
  updatedAt: string;
}

export interface ProposalPipelineStatus {
  resumeFromPhase: PipelinePhase;
  completedPhases: PipelinePhase[];
  isComplete: boolean;
  canResume: boolean;
  lastCompletedPhase?: PipelinePhase | null;
  lastFailedPhase?: PipelinePhase | null;
  lastError?: string | null;
  inProgressPhase?: PipelinePhase | null;
  phaseLabels: Record<string, string>;
  checkpoint?: ProposalPipelineCheckpoint | null;
}

function countVerifyTags(draft: ProposalOutline | null): number {
  if (!draft) return 0;
  return draft.sections.reduce((total, section) => {
    const matches = section.content?.match(/\[VERIFY:/gi);
    return total + (matches?.length ?? 0);
  }, 0);
}

export function inferResumePhaseFromBlocker(blocker: string): PipelinePhase {
  const lower = blocker.toLowerCase();
  if (lower.includes("budget") || lower.includes("phase 3.5")) {
    return "phase-3-5-budget";
  }
  if (lower.includes("pre-submit") || lower.includes("phase 4")) {
    return "phase-4-review";
  }
  if (lower.includes("proof point") || lower.includes("evidence corpus")) {
    return "phase-2";
  }
  if (lower.includes("phase 3")) return "phase-3";
  if (lower.includes("verify") || lower.includes("placeholder")) {
    return "phase-3-6-self-edit";
  }
  return "phase-3-5-budget";
}

function selfEditConsideredComplete(
  draft: ProposalOutline | null,
  research: ProposalResearch | null
): boolean {
  if (!research?.pipelineCheckpoint) return false;
  const cp = research.pipelineCheckpoint;
  if (cp.lastFailedPhase === "phase-3-6-self-edit") {
    const err = (cp.lastError ?? "").toLowerCase();
    if (err.includes("verify") || err.includes("placeholder")) {
      return phaseIsComplete(draft, research, "phase-3");
    }
  }
  if (cp.lastCompletedPhase) {
    return phaseIndex(cp.lastCompletedPhase) >= phaseIndex("phase-3-6-self-edit");
  }
  return false;
}

function phaseIndex(phase: PipelinePhase): number {
  if (phase === "complete") return PIPELINE_PHASE_ORDER.length;
  return PIPELINE_PHASE_ORDER.indexOf(phase);
}

export function phaseIsComplete(
  draft: ProposalOutline | null,
  research: ProposalResearch | null,
  phase: PipelinePhase
): boolean {
  if (phase === "sections-1-3") {
    return staticSections1to3Complete(draft);
  }
  if (!research) return false;

  if (phase === "phase-2") {
    return Boolean(research.evidenceCorpus?.length && research.rfpSections?.length);
  }
  if (phase === "phase-3") {
    if (!draft || !research.rfpSections?.length) return false;
    const mappedIds = new Set(research.rfpSections.map((s) => s.id));
    const filled = draft.sections.filter(
      (s) => mappedIds.has(s.id) && s.content?.trim()
    ).length;
    return filled >= Math.max(1, Math.floor(mappedIds.size * 0.85));
  }
  if (phase === "phase-3-6-self-edit") {
    if (selfEditConsideredComplete(draft, research)) return true;
    const cp = research.pipelineCheckpoint;
    if (cp?.lastFailedPhase === phase) {
      const err = (cp.lastError ?? "").toLowerCase();
      if (
        (err.includes("verify") || err.includes("placeholder")) &&
        phaseIsComplete(draft, research, "phase-3")
      ) {
        return true;
      }
      return false;
    }
    return false;
  }
  if (phase === "phase-3-5-budget") {
    return Boolean(research.budget);
  }
  if (phase === "phase-4-review") {
    return Boolean(research.presubmitReview);
  }
  return false;
}

export function resolveResumePhase(
  draft: ProposalOutline | null,
  research: ProposalResearch | null
): PipelinePhase {
  if (!staticSections1to3Complete(draft)) {
    return "sections-1-3";
  }

  const cp = research?.pipelineCheckpoint;
  if (cp?.lastFailedPhase && PIPELINE_PHASE_ORDER.includes(cp.lastFailedPhase)) {
    if (cp.lastFailedPhase === "phase-3-6-self-edit") {
      const err = (cp.lastError ?? "").toLowerCase();
      if (
        (err.includes("verify") || err.includes("placeholder")) &&
        !phaseIsComplete(draft, research, "phase-3-5-budget")
      ) {
        return "phase-3-5-budget";
      }
    }
    return cp.lastFailedPhase;
  }
  if (cp?.inProgressPhase && PIPELINE_PHASE_ORDER.includes(cp.inProgressPhase)) {
    return cp.inProgressPhase;
  }
  if (cp?.resumeFromPhase && PIPELINE_PHASE_ORDER.includes(cp.resumeFromPhase)) {
    if (!phaseIsComplete(draft, research, cp.resumeFromPhase)) {
      return cp.resumeFromPhase;
    }
  }
  for (const phase of PIPELINE_PHASE_ORDER) {
    if (!phaseIsComplete(draft, research, phase)) {
      return phase;
    }
  }
  if (draft && research) {
    if (!research.presubmitReview) return "phase-4-review";
    if (!research.proofPoints?.length) return "phase-2";
  }
  return "complete";
}

export function buildPipelineStatus(
  draft: ProposalOutline | null,
  research: ProposalResearch | null,
  serverStatus?: ProposalPipelineStatus | null
): ProposalPipelineStatus {
  if (serverStatus) {
    return serverStatus;
  }
  const resumeFromPhase = resolveResumePhase(draft, research);
  const completedPhases = PIPELINE_PHASE_ORDER.filter((phase) =>
    phaseIsComplete(draft, research, phase)
  );
  const cp = research?.pipelineCheckpoint;
  return {
    resumeFromPhase,
    completedPhases,
    isComplete: resumeFromPhase === "complete",
    canResume:
      Boolean(draft) &&
      Boolean(
        cp?.lastFailedPhase || resumeFromPhase !== "complete"
      ),
    lastCompletedPhase: cp?.lastCompletedPhase ?? completedPhases.at(-1) ?? null,
    lastFailedPhase: cp?.lastFailedPhase ?? null,
    lastError: cp?.lastError ?? null,
    inProgressPhase: cp?.inProgressPhase ?? null,
    phaseLabels: PIPELINE_PHASE_LABELS,
    checkpoint: cp ?? null,
  };
}

export function shouldRunPhase(
  phase: PipelinePhase,
  resumeFrom: PipelinePhase
): boolean {
  if (resumeFrom === "complete") return false;
  return phaseIndex(phase) >= phaseIndex(resumeFrom);
}

export function pipelineResumeMessage(
  status: ProposalPipelineStatus,
  options?: { blocker?: string | null }
): string {
  if (options?.blocker) {
    const phase = inferResumePhaseFromBlocker(options.blocker);
    const label = PIPELINE_PHASE_LABELS[phase];
    return `${options.blocker} Resume from ${label}.`;
  }
  if (status.lastFailedPhase) {
    const failed = PIPELINE_PHASE_LABELS[status.lastFailedPhase];
    return `Stopped at ${failed}${status.lastError ? ` (${status.lastError.slice(0, 120)})` : ""}. Resume to retry.`;
  }
  if (status.resumeFromPhase === "complete") {
    return "All pipeline phases finished. Review the manuscript or run pre-submit auto-fix if issues remain.";
  }
  const label = PIPELINE_PHASE_LABELS[status.resumeFromPhase];
  if (status.inProgressPhase) {
    return `Interrupted during ${PIPELINE_PHASE_LABELS[status.inProgressPhase]}. Resume from ${label}.`;
  }
  return `Resume from ${label}.`;
}
