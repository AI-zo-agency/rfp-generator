import type { ProposalOutline, ProposalResearch } from "@/types/proposal";
import { staticSections1to3Complete } from "@/lib/proposal-draft";

export type PipelinePhase =
  | "sections-1-3"
  | "phase-2"
  | "phase-3"
  | "phase-3-5-budget"
  | "phase-3-6-self-edit"
  | "phase-4-review"
  | "complete";

export type PipelineInProgressPhase = PipelinePhase | typeof FULFILL_SCAN_PHASE;

export const PIPELINE_PHASE_ORDER: PipelinePhase[] = [
  "sections-1-3",
  "phase-2",
  "phase-3",
  "phase-3-5-budget",
  "phase-3-6-self-edit",
  "phase-4-review",
];

export const PIPELINE_PHASE_LABELS: Record<PipelinePhase, string> = {
  "sections-1-3": "Sections 1–3",
  "phase-2": "Phase 2 intelligence",
  "phase-3": "Phase 3 drafting",
  "phase-3-6-self-edit": "Senior editor polish",
  "phase-3-5-budget": "Budget build",
  "phase-4-review": "Pre-submit review",
  complete: "Complete",
};

export interface ProposalPipelineCheckpoint {
  lastCompletedPhase?: PipelinePhase | null;
  inProgressPhase?: PipelineInProgressPhase | null;
  lastFailedPhase?: PipelinePhase | null;
  lastError?: string | null;
  resumeFromPhase?: PipelinePhase | null;
  activityLabel?: string | null;
  activityDetail?: string | null;
  stepIndex?: number | null;
  stepTotal?: number | null;
  updatedAt: string;
}

export const FULFILL_SCAN_PHASE = "fulfill-scan";

/** Live substep shown while Senior editor polish runs (maps to backend activity labels).
 * Dedupe, coverage/compliance ticketing, VERIFY-vs-RFP scrubbing, and legal gates now
 * run as one consolidated backend pass (ticket emission + VERIFY scrub happen
 * concurrently), so there is a single card rather than a 3-step sequence. */
export const SENIOR_EDITOR_SUBSTEPS = [
  {
    id: "coverage-compliance-verify",
    label: "Coverage, compliance & VERIFY scrub",
    hint: "RFP gaps, required gov/buyer policies, and unneeded [VERIFY] tags — scanned together",
  },
] as const;

export function seniorEditorSubstepIndex(
  _activityLabel: string | null | undefined,
  _stepIndex?: number | null
): number {
  // Only one substep exists now — always the active card while the phase runs.
  return 0;
}

const SECTION_DRAFT_FAILURE_MARKER =
  "[VERIFY: Section drafting failed — needs manual regeneration]";

/** Mirrors backend is_duplicate_static_rfp_section — skipped by Phase 3 drafting. */
export function isDuplicateStaticRfpSection(title: string): boolean {
  const t = title.trim();
  if (!t) return false;

  const coveredTitle = [
    /\bwho\s+we\s+are\b/i,
    /\bour\s+promise\b/i,
    /\bcompany\s+history\b/i,
    /\bfirm\s+history\b/i,
    /\bfirm\s+(?:overview|profile|background)\b/i,
    /\babout\s+(?:the\s+)?(?:firm|agency|company|proposer|vendor)\b/i,
    /\bclient\s+roster\b/i,
    /\bcore\s+services\b/i,
    /\borganizational?\s+structure\b/i,
    /\bbusiness\s+information\b/i,
    /\bcertifications?\b/i,
    /\binsurance\s+information\b/i,
    /\bcompany\s+overview\b/i,
    /\bteam\s+overview\b(?:\s*[—\-–:].*)?\b(bios?|resumes?|personnel|contract\s+manager|point\s+of\s+contact|primary\s+contact|staff(?:ing)?)\b/i,
    /^\s*team\s+overview\s*$/i,
    /\bpersonnel\s+bios?(?:\s*\/\s*resumes?)?\b/i,
    /\b(?:staff|team)\s+(?:member\s+)?(?:bios?|resumes?)\b/i,
  ].some((p) => p.test(t));

  if (coveredTitle) {
    if (/\b(sample\s+work|portfolio|minimum\s+two|recent\s+campaign)\b/i.test(t)) {
      return false;
    }
    if (
      /\b(agency\s+requirements?|capability\s+matrix|service\s+capability|scope\s+of\s+work|statement\s+of\s+work)\b/i.test(
        t
      )
    ) {
      return false;
    }
    return true;
  }

  const patterns = [
    /section\s*1\b/i,
    /company\s+overview/i,
    /section\s*2\b/i,
    /team\s+(overview|bios|qualifications|experience)/i,
    /section\s*3\b/i,
    /(case\s+stud|our\s+work|past\s+performance|relevant\s+experience)/i,
  ];
  const hits = patterns.filter((p) => p.test(t)).length;
  if (hits >= 2) return true;
  if (/section\s*[123]\b/i.test(t) && /overview|company|team|work|case/i.test(t)) {
    return true;
  }
  if (/^section\s*1\s*[—\-–:]\s*company\s+overview$/i.test(t)) return true;
  if (/^section\s*2\s*[—\-–:]\s*team\s+overview$/i.test(t)) return true;
  if (/^section\s*3\s*[—\-–:]\s*our\s+work/i.test(t)) return true;
  return false;
}

function phase3SectionContentUsable(content: string | undefined | null): boolean {
  const text = content?.trim() ?? "";
  if (!text) return false;
  if (text === SECTION_DRAFT_FAILURE_MARKER) return false;
  return true;
}

/** UI chip labels for the Scan RFP button (full RFP update). */
export const FULFILL_SCAN_STEP_LABELS = [
  "Closing & submission tabs",
  "RFP structure (all scored sections)",
  "Requirement ledger (merge / cut / add)",
  "DQ & gov-policy gate (agentic loop)",
  "Budget (regen if missing + thorough)",
  "Consistency repairs",
  "Contractor KPIs (Section 2.3)",
  "KB fact-check (Supermemory)",
  "RFP contradiction check (LLM)",
  "Remove optional [VERIFY] tags",
  "Pre-submit refresh",
] as const;

export const FULL_PROPOSAL_STEP_LABELS: { phase: PipelinePhase; label: string }[] = [
  { phase: "sections-1-3", label: "Sections 1–3" },
  { phase: "phase-2", label: "Intelligence" },
  { phase: "phase-3", label: "RFP tabs" },
  { phase: "phase-3-5-budget", label: "Budget" },
  { phase: "phase-3-6-self-edit", label: "Senior editor" },
  { phase: "phase-4-review", label: "Review" },
];

export interface ProposalPipelineStatus {
  resumeFromPhase: PipelinePhase;
  completedPhases: PipelinePhase[];
  isComplete: boolean;
  canResume: boolean;
  lastCompletedPhase?: PipelinePhase | null;
  lastFailedPhase?: PipelinePhase | null;
  lastError?: string | null;
  inProgressPhase?: PipelineInProgressPhase | null;
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
    const readiness =
      research.proposalExecutionPlan?.validation?.readinessStatus;
    if (readiness) {
      return readiness === "ready" && Boolean(research.rfpSections?.length);
    }
    // Legacy caches created before intelligence layer
    return Boolean(research.evidenceCorpus?.length && research.rfpSections?.length);
  }
  if (phase === "phase-3") {
    if (!draft || !research.rfpSections?.length) return false;
    const draftableIds = new Set(
      research.rfpSections
        .filter((s) => !isDuplicateStaticRfpSection(s.title))
        .map((s) => s.id)
    );
    if (draftableIds.size === 0) return false;
    const filled = draft.sections.filter(
      (s) => draftableIds.has(s.id) && phase3SectionContentUsable(s.content)
    ).length;
    return filled >= draftableIds.size;
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
    if (!research.budget) return false;
    // Mirror backend: a failed budget with grounding/rate-card markers is not complete.
    const cp = research.pipelineCheckpoint;
    if (cp?.lastFailedPhase === "phase-3-5-budget") {
      const err = (cp.lastError ?? "").toLowerCase();
      if (
        err.includes("grounding") ||
        err.includes("pricing contradiction") ||
        err.includes("unresolved pricing") ||
        err.includes("rate card unusable")
      ) {
        return false;
      }
    }
    // Artifact alone is not enough — checkpoint must have reached budget.
    const last = cp?.lastCompletedPhase;
    if (!last) return false;
    const order = PIPELINE_PHASE_ORDER;
    const lastIdx = order.indexOf(last as PipelinePhase);
    const budgetIdx = order.indexOf("phase-3-5-budget");
    return lastIdx >= 0 && budgetIdx >= 0 && lastIdx >= budgetIdx;
  }
  if (phase === "phase-4-review") {
    if (!research.presubmitReview) return false;
    const last = research.pipelineCheckpoint?.lastCompletedPhase;
    if (!last) return false;
    const order = PIPELINE_PHASE_ORDER;
    const lastIdx = order.indexOf(last as PipelinePhase);
    const reviewIdx = order.indexOf("phase-4-review");
    return lastIdx >= 0 && reviewIdx >= 0 && lastIdx >= reviewIdx;
  }
  return false;
}

/** Prefer server completedPhases; local phaseIsComplete is display/fallback only. */
export function phaseCompleteOnServer(
  status: ProposalPipelineStatus | null | undefined,
  phase: PipelinePhase
): boolean | null {
  if (!status) return null;
  return status.completedPhases.includes(phase);
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
  if (cp?.inProgressPhase && PIPELINE_PHASE_ORDER.includes(cp.inProgressPhase as PipelinePhase)) {
    return cp.inProgressPhase as PipelinePhase;
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
    const planReady =
      research.proposalExecutionPlan?.validation?.readinessStatus === "ready";
    if (!planReady && !research.proofPoints?.length) return "phase-2";
  }
  return "complete";
}

/** True only when there is real progress to continue — not an empty post-Reset shell. */
export function hasResumablePipelineProgress(
  draft: ProposalOutline | null,
  research: ProposalResearch | null
): boolean {
  const cp = research?.pipelineCheckpoint;
  if (
    cp?.lastCompletedPhase ||
    cp?.lastFailedPhase ||
    cp?.inProgressPhase
  ) {
    return true;
  }
  if (
    (research?.rfpSections?.length ?? 0) > 0 ||
    (research?.evidenceCorpus?.length ?? 0) > 0 ||
    Boolean(research?.budget) ||
    Boolean(research?.presubmitReview)
  ) {
    return true;
  }
  if (draft?.sections.some((s) => s.content?.trim())) {
    return true;
  }
  return false;
}

export function buildPipelineStatus(
  draft: ProposalOutline | null,
  research: ProposalResearch | null,
  serverStatus?: ProposalPipelineStatus | null
): ProposalPipelineStatus {
  const hasProgress = hasResumablePipelineProgress(draft, research);
  if (serverStatus) {
    return {
      ...serverStatus,
      // Never treat an empty post-Reset shell as resumable, even if the
      // server still reports canResume from a stale checkpoint.
      canResume: hasProgress && serverStatus.canResume && !serverStatus.isComplete,
    };
  }
  // Offline / direct-Supabase fallback only. Orchestration must use server status.
  const cp = research?.pipelineCheckpoint;
  const resumeFromPhase =
    (cp?.resumeFromPhase && PIPELINE_PHASE_ORDER.includes(cp.resumeFromPhase)
      ? cp.resumeFromPhase
      : null) ??
    (cp?.lastFailedPhase && PIPELINE_PHASE_ORDER.includes(cp.lastFailedPhase)
      ? cp.lastFailedPhase
      : null) ??
    (cp?.inProgressPhase &&
    PIPELINE_PHASE_ORDER.includes(cp.inProgressPhase as PipelinePhase)
      ? (cp.inProgressPhase as PipelinePhase)
      : null) ??
    resolveResumePhase(draft, research);
  const completedPhases = PIPELINE_PHASE_ORDER.filter((phase) =>
    phaseIsComplete(draft, research, phase)
  );
  return {
    resumeFromPhase,
    completedPhases,
    isComplete: resumeFromPhase === "complete",
    // Empty default outline after Reset is NOT resumable — that is a fresh Generate.
    canResume:
      hasProgress &&
      Boolean(cp?.lastFailedPhase || resumeFromPhase !== "complete"),
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

export function inProgressPhaseLabel(phase: PipelineInProgressPhase): string {
  if (phase === FULFILL_SCAN_PHASE) return "Complete & clean draft";
  return PIPELINE_PHASE_LABELS[phase];
}

const IN_PROGRESS_STALE_MS = 900_000;
const DRAFT_LIVENESS_MS = 600_000;

function isoAgeMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Date.now() - t : null;
}

/**
 * Align checkpoint display for every viewer (reload / second account) without
 * mutating Supabase — mirrors backend heal + stale detection on read.
 */
export function normalizeCheckpointForDisplay(
  draft: ProposalOutline | null,
  research: ProposalResearch | null
): ProposalResearch | null {
  if (!research?.pipelineCheckpoint) return research;
  const cp = research.pipelineCheckpoint;
  const draftAge = isoAgeMs(draft?.updatedAt ?? null);
  const draftLive = draftAge !== null && draftAge < DRAFT_LIVENESS_MS;

  // Do NOT clear sections-1-3 in-progress just because the draft looks complete.
  // Force regenerate keeps prior complete content until the job finishes; clearing
  // here would make async start+poll think the phase already ended. Stale
  // in-progress flags are healed by the age-based block below.

  if (!cp.inProgressPhase && cp.lastFailedPhase && cp.lastError) {
    const err = cp.lastError.toLowerCase();
    if (
      (err.includes("interrupted") || err.includes("connection lost")) &&
      draftLive
    ) {
      return {
        ...research,
        pipelineCheckpoint: {
          ...cp,
          inProgressPhase: cp.lastFailedPhase,
          lastFailedPhase: null,
          lastError: null,
        },
      };
    }
  }

  if (cp.inProgressPhase) {
    const cpAge = isoAgeMs(cp.updatedAt);
    const manuscriptLive =
      draftAge !== null && draftAge < IN_PROGRESS_STALE_MS;
    if (manuscriptLive) {
      return research;
    }
    if (cpAge !== null && cpAge >= IN_PROGRESS_STALE_MS) {
      const failed = PIPELINE_PHASE_ORDER.includes(
        cp.inProgressPhase as PipelinePhase
      )
        ? (cp.inProgressPhase as PipelinePhase)
        : cp.lastFailedPhase ?? "phase-3";
      return {
        ...research,
        pipelineCheckpoint: {
          ...cp,
          inProgressPhase: null,
          lastFailedPhase: failed,
          lastError:
            cp.lastError ??
            "Phase interrupted (connection lost or server restarted). Resume to continue.",
        },
      };
    }
  }

  return research;
}

/** Soft status when HTTP finished but checkpoint still shows a phase in flight. */
export function pipelineServerStillWorkingMessage(
  phase: PipelineInProgressPhase
): string {
  return `Still generating ${inProgressPhaseLabel(phase)}. New sections will show up here as they finish.`;
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
    return `Interrupted during ${inProgressPhaseLabel(status.inProgressPhase)}. Resume from ${label}.`;
  }
  return `Resume from ${label}.`;
}
