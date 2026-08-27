import type {
  ProposalOutline,
  OutlineSection,
  ProposalResearch,
  ProposalBudget,
  PreSubmitReview,
  PreSubmitAutoFixReport,
} from "@/types/proposal";

interface ApiProposalSection {
  id: string;
  title: string;
  pageLimit?: number;
  wordTarget: number;
  required: boolean;
  custom: boolean;
  content: string;
  status: OutlineSection["status"];
  source: OutlineSection["source"];
  mode?: OutlineSection["mode"];
  designerNote?: string;
  kbRefs?: string[];
}

export interface ApiProposalDraft {
  rfpId: string;
  sections: ApiProposalSection[];
  updatedAt: string;
  generatedAt?: string | null;
  provider?: string | null;
  googleDocUrl?: string | null;
  googleDocId?: string | null;
  googleDocExportedAt?: string | null;
  snapshots?: ProposalOutline["snapshots"];
  lastFulfillReport?: Record<string, unknown>;
  selectedKeyPersonas?: string[];
}

export function apiDraftToOutline(draft: ApiProposalDraft): ProposalOutline {
  return {
    updatedAt: draft.updatedAt,
    sections: draft.sections,
    googleDocUrl: draft.googleDocUrl ?? null,
    googleDocId: draft.googleDocId ?? null,
    googleDocExportedAt: draft.googleDocExportedAt ?? null,
    snapshots: (draft.snapshots ?? []).map((s) => ({
      ...s,
      sections: s.sections ?? [],
    })),
    lastFulfillReport: draft.lastFulfillReport ?? undefined,
    selectedKeyPersonas: draft.selectedKeyPersonas ?? [],
  };
}

/** FastAPI may return detail as string, list of validation errors, or object. */
export function formatApiDetail(
  detail: unknown,
  fallback = "Request failed"
): string {
  if (detail == null) return fallback;
  if (typeof detail === "string") return detail.trim() || fallback;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const row = item as { msg?: unknown; loc?: unknown; message?: unknown };
          const msg = row.msg ?? row.message;
          if (typeof msg === "string" && msg.trim()) {
            const loc = Array.isArray(row.loc)
              ? row.loc.filter((p) => p !== "body").join(".")
              : "";
            return loc ? `${loc}: ${msg}` : msg;
          }
        }
        return null;
      })
      .filter((p): p is string => Boolean(p));
    return parts.length > 0 ? parts.join("; ") : fallback;
  }
  if (typeof detail === "object") {
    const row = detail as { message?: unknown; msg?: unknown; detail?: unknown };
    if (typeof row.message === "string") return row.message;
    if (typeof row.msg === "string") return row.msg;
    if (typeof row.detail === "string") return row.detail;
  }
  return fallback;
}

function slimSnapshotsForSave(
  snapshots: ProposalOutline["snapshots"]
): ProposalOutline["snapshots"] {
  if (!snapshots?.length) return [];
  return snapshots.map((s) => ({
    savedAt: s.savedAt,
    label: s.label,
    scanSummary: s.scanSummary,
    sectionCount: s.sections?.length ?? s.sectionCount ?? 0,
    sections: [],
  }));
}

export function outlineToApiDraft(
  rfpId: string,
  outline: ProposalOutline
): ApiProposalDraft {
  return {
    rfpId,
    updatedAt: outline.updatedAt,
    googleDocUrl: outline.googleDocUrl ?? null,
    googleDocId: outline.googleDocId ?? null,
    googleDocExportedAt: outline.googleDocExportedAt ?? null,
    snapshots: slimSnapshotsForSave(outline.snapshots),
    lastFulfillReport: outline.lastFulfillReport ?? undefined,
    // Always send explicit list so Clear/Reset ([] ) is not treated as "omit"
    // and re-filled from a prior selection on the server.
    selectedKeyPersonas: outline.selectedKeyPersonas ?? [],
    sections: outline.sections.map((s) => ({
      ...s,
      source:
        s.source === "custom"
          ? "generated"
          : (s.source as ApiProposalSection["source"]),
    })),
  };
}

import {
  buildPipelineStatus,
  normalizeCheckpointForDisplay,
  phaseCompleteOnServer,
  phaseIndex,
  phaseIsComplete,
  PIPELINE_PHASE_ORDER,
  shouldRunPhase,
  shouldSkipCompletedPhase,
  type PipelinePhase,
  type PipelineInProgressPhase,
  type ProposalPipelineStatus,
  FULFILL_SCAN_PHASE,
  ALIGN_RFP_OUTLINE_PHASE,
  PACKET_REDISTRIBUTE_PHASE,
} from "./proposal-pipeline-checkpoint";
import { staticSections1to3Complete } from "./proposal-draft";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "./proposal-stage-timeout";

export type { PipelinePhase, ProposalPipelineStatus };
export {
  buildPipelineStatus,
  PIPELINE_PHASE_LABELS,
  inferResumePhaseFromBlocker,
  pipelineResumeMessage,
  pipelineServerStillWorkingMessage,
  resolveResumePhase,
} from "./proposal-pipeline-checkpoint";

/** Long timeout for staged proposal generation (browser → Next API route). */
function proposalPostInit(signal?: AbortSignal): RequestInit {
  const init: RequestInit = { method: "POST" };
  if (signal) {
    if (PROPOSAL_STAGE_TIMEOUT_MS > 0 && typeof AbortSignal.any === "function") {
      init.signal = AbortSignal.any([
        signal,
        AbortSignal.timeout(PROPOSAL_STAGE_TIMEOUT_MS),
      ]);
    } else {
      init.signal = signal;
    }
    return init;
  }
  // No client-side timer by default — wait for Next/backend (user cancel still works via signal).
  if (PROPOSAL_STAGE_TIMEOUT_MS > 0) {
    init.signal = AbortSignal.timeout(PROPOSAL_STAGE_TIMEOUT_MS);
  }
  return init;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    const err = new DOMException("Proposal generation aborted", "AbortError");
    throw err;
  }
}

function throwIfProposalStopped(res: Response, data: { detail?: string }): void {
  // Only cooperative Stop / cancel conflicts — never "another job is running"
  // (that is a Celery chain race; callers attach and wait instead).
  if (res.status !== 409) return;
  const detail = data.detail ?? "";
  if (/already running \(/i.test(detail)) {
    throw new Error(detail || "Another proposal job is already running");
  }
  throw new DOMException(
    detail || "Proposal generation stopped",
    "AbortError"
  );
}

function asGeneratePipelinePhase(
  value: string | null | undefined
): PipelinePhase | null {
  if (!value || value === "complete") return null;
  return (PIPELINE_PHASE_ORDER as string[]).includes(value)
    ? (value as PipelinePhase)
    : null;
}

/** Parse FastAPI "Another proposal job is already running (phase-3-5-budget)." */
function parseAlreadyRunningJobType(detail: string): string | null {
  const match = detail.match(/already running \(([^)]+)\)/i);
  const jobType = match?.[1]?.trim();
  return jobType || null;
}

/** Tell backend to stop LLM/Supermemory and save pipeline checkpoint. */
export async function stopProposalGeneration(rfpId: string): Promise<void> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/stop`, {
    method: "POST",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok && res.status !== 409) {
    let detail = "Stop request failed";
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      // keep default
    }
    throw new Error(detail);
  }
}

export async function clearProposalGenerationStop(rfpId: string): Promise<void> {
  await fetch(`/api/rfps/${rfpId}/proposal/generation/clear-stop`, {
    method: "POST",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
}

const VERIFY_TAG_RE = /\[VERIFY:/gi;

export function countVerifyTagsInOutline(outline: ProposalOutline): number {
  return outline.sections.reduce((total, section) => {
    const matches = section.content?.match(VERIFY_TAG_RE);
    return total + (matches?.length ?? 0);
  }, 0);
}

export function validateStagedProposalComplete(
  draft: ProposalOutline,
  research: ProposalResearch
): string | null {
  const planReady =
    research.proposalExecutionPlan?.validation?.readinessStatus === "ready";
  if (!planReady && !research.evidenceCorpus?.length) {
    return "Phase 2 incomplete — Proposal Execution Plan not ready.";
  }
  if (!research.rfpSections?.length) {
    return "Phase 2 incomplete — no proposal sections planned.";
  }
  if (!phaseIsComplete(draft, research, "phase-3")) {
    return "Phase 3 incomplete — RFP tabs are not fully drafted.";
  }
  if (!research.proofPoints?.length && !planReady) {
    return "Phase 2 incomplete — no proof points matched (check case-study KB).";
  }
  if (!research.budget) {
    return "Phase 3.5 incomplete — budget not generated.";
  }
  if (!research.presubmitReview) {
    return "Phase 4 incomplete — pre-submit review not run.";
  }
  const verifyCount = countVerifyTagsInOutline(draft);
  if (verifyCount > 0) {
    return `Manuscript still has ${verifyCount} unresolved [VERIFY] placeholder(s). Use Finalize gaps or fix manually before upload.`;
  }
  return null;
}

export type FullProposalProgress =
  | "sections-1-3"
  | "phase-2"
  | "phase-3"
  | "phase-3-6-self-edit"
  | "phase-3-5-budget"
  | "phase-4-review"
  | "recovering";

/** Map server checkpoint in-flight phase to UI progress when the client HTTP call already ended. */
export function fullProposalProgressFromInFlight(
  phase: PipelineInProgressPhase | null | undefined
): FullProposalProgress | null {
  if (!phase || phase === FULFILL_SCAN_PHASE || phase === ALIGN_RFP_OUTLINE_PHASE || phase === PACKET_REDISTRIBUTE_PHASE) {
    return null;
  }
  switch (phase) {
    case "sections-1-3":
    case "phase-2":
    case "phase-3":
    case "phase-3-6-self-edit":
    case "phase-3-5-budget":
    case "phase-4-review":
      return phase;
    default:
      // Never invent "RFP tabs" for standalone jobs (Align, Scan, etc.).
      return null;
  }
}

export function countSectionsWithContent(outline: ProposalOutline): number {
  return outline.sections.filter((s) => s.content?.trim()).length;
}

/** Poll saved draft while a long backend phase runs — surfaces each section as it lands. */
export function startLiveDraftPolling(
  rfpId: string,
  onDraftUpdate: (draft: ProposalOutline) => void,
  onResearchUpdate?: (research: ProposalResearch | null) => void
): () => void {
  let lastFingerprint = "";
  let lastResearchAt = "";
  let stopped = false;

  const fingerprint = (draft: ProposalOutline) => {
    const parts = draft.sections.map(
      (s) => `${s.id}:${s.status}:${(s.content || "").length}:${(s.content || "").slice(0, 40)}`
    );
    return `${draft.updatedAt}|${parts.join("|")}`;
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const snapshot = await fetchProposalDraft(rfpId);
      if (snapshot.draft) {
        const next = fingerprint(snapshot.draft);
        if (next !== lastFingerprint) {
          lastFingerprint = next;
          onDraftUpdate(snapshot.draft);
        }
      }
      const cpAt = snapshot.research?.pipelineCheckpoint?.updatedAt ?? "";
      if (onResearchUpdate && cpAt !== lastResearchAt) {
        lastResearchAt = cpAt;
        onResearchUpdate(snapshot.research ?? null);
      } else if (
        onResearchUpdate &&
        !snapshot.draft &&
        snapshot.research?.pipelineCheckpoint?.inProgressPhase
      ) {
        onResearchUpdate(snapshot.research);
      }
    } catch {
      // Ignore transient poll errors during long runs.
    }
  };

  void poll();
  const timer = setInterval(() => {
    void poll();
  }, LIVE_DRAFT_POLL_INTERVAL_MS);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

function withLiveDraftPolling<T>(
  rfpId: string,
  onDraftUpdate: ((draft: ProposalOutline) => void) | undefined,
  run: () => Promise<T>,
  onResearchUpdate?: (research: ProposalResearch | null) => void
): Promise<T> {
  if (!onDraftUpdate) {
    return run();
  }
  const stop = startLiveDraftPolling(rfpId, onDraftUpdate, onResearchUpdate);
  return run().finally(stop);
}

/** Load draft from DB when HTTP failed but backend may have finished saving. */
export async function recoverProposalDraftIfSaved(
  rfpId: string,
  options?: { minSectionsWithContent?: number }
): Promise<{ draft: ProposalOutline; research: ProposalResearch | null } | null> {
  const min = options?.minSectionsWithContent ?? 3;
  const { draft, research } = await fetchProposalDraft(rfpId);
  if (!draft || countSectionsWithContent(draft) < min) {
    return null;
  }
  return { draft, research };
}

export async function runPhase3_5BudgetWithRecovery(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
  recoveredFromDraft: boolean;
}> {
  throwIfAborted(signal);
  const startedAt = await captureProposalTimestamps(rfpId);
  try {
    const result = await runPhase3_5Budget(rfpId, signal);
    return { ...result, recoveredFromDraft: false };
  } catch (error) {
    if (signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw error;
    }
    const message = error instanceof Error ? error.message : String(error);
    // Hard validation / business-rule failures will never recover by polling.
    const hardFail =
      /BUDGET VALIDATION FAILED|BUDGET EDITOR FAILED|project management lines|Unprocessable|422/i.test(
        message
      );
    if (hardFail) {
      throw error;
    }
    const polled = await pollForBackendStageSave(rfpId, startedAt, {
      requireBudget: true,
    });
    if (polled?.research.budget) {
      return {
        budget: polled.research.budget,
        research: polled.research,
        draft: polled.draft,
        recoveredFromDraft: true,
      };
    }
    const recovered = await fetchProposalDraft(rfpId);
    if (recovered.research?.budget && recovered.draft) {
      return {
        budget: recovered.research.budget,
        research: recovered.research,
        draft: recovered.draft,
        recoveredFromDraft: true,
      };
    }
    throw error;
  }
}

export async function runPhase3_6SelfEditWithRecovery(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline;
  research: ProposalResearch;
  recoveredFromDraft: boolean;
}> {
  throwIfAborted(signal);
  const startedAt = await captureProposalTimestamps(rfpId);
  try {
    const result = await runPhase3_6SelfEdit(rfpId, signal);
    return { ...result, recoveredFromDraft: false };
  } catch (error) {
    if (signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw error;
    }
    const polled = await pollForBackendStageSave(rfpId, startedAt);
    if (polled) {
      return { ...polled, recoveredFromDraft: true };
    }
    throw error;
  }
}

const STAGE_POLL_INTERVAL_MS = 4_000;
const STAGE_POLL_MAX_MS = 22 * 60 * 1000;
const LIVE_DRAFT_POLL_INTERVAL_MS = 4_000;
/** If checkpoint says in-flight but timestamps never move, backend was killed — don't block resume. */
const IN_FLIGHT_STALE_MS = 90_000;
/** Start endpoints return 202 immediately — keep this short. */
const PHASE_START_TIMEOUT_MS = 60_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

type ProposalJobStatus = {
  rfpId?: string;
  jobType?: string;
  status?: "queued" | "running" | "completed" | "failed" | "cancelled";
  error?: string | null;
  startedAt?: string;
  finishedAt?: string | null;
};

export type ActiveProposalJob = {
  rfpId: string;
  title: string;
  jobType: string;
  status: "queued" | "running";
  startedAt: string;
};

/** Every proposal-pipeline job currently queued or running, across all RFPs
 * — lets the UI explain a queued job instead of showing "Generating..."
 * with no visible progress and no reason why. */
export async function listActiveProposalJobs(): Promise<ActiveProposalJob[]> {
  try {
    const res = await fetchWithTimeout(
      `/api/proposals/jobs/active`,
      { cache: "no-store" },
      15_000
    );
    if (!res.ok) return [];
    const data = (await res.json()) as { jobs?: ActiveProposalJob[] };
    return data.jobs ?? [];
  } catch {
    return [];
  }
}

export type { ProposalJobStatus };

export async function getProposalJobStatus(
  rfpId: string
): Promise<ProposalJobStatus | null> {
  return fetchProposalJobStatus(rfpId);
}

async function startProposalPhaseJob(
  path: string,
  signal?: AbortSignal,
  options?: { body?: string; phase?: PipelinePhase | string }
): Promise<
  | { mode: "async"; alreadyRunning: boolean }
  | {
      mode: "sync";
      draft?: ApiProposalDraft;
      research?: ProposalResearch;
      budget?: ProposalBudget;
      review?: PreSubmitReview;
    }
> {
  throwIfAborted(signal);
  const init: RequestInit = {
    method: "POST",
    headers: {
      Accept: "application/json",
      ...(options?.body ? { "Content-Type": "application/json" } : {}),
    },
    cache: "no-store",
    body: options?.body,
  };
  const timers: AbortSignal[] = [AbortSignal.timeout(PHASE_START_TIMEOUT_MS)];
  if (signal) timers.push(signal);
  init.signal =
    typeof AbortSignal.any === "function" ? AbortSignal.any(timers) : timers[0];

  const res = await fetch(path, init);
  const text = await res.text();
  let data: {
    detail?: string;
    ok?: boolean;
    started?: boolean;
    alreadyRunning?: boolean;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
    budget?: ProposalBudget;
    review?: PreSubmitReview;
  } = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server when starting proposal phase.");
  }

  // Legacy sync backend still returns 200 with a full payload.
  if (res.status === 200 && (data.draft || data.research || data.budget || data.review)) {
    return {
      mode: "sync",
      draft: data.draft,
      research: data.research,
      budget: data.budget,
      review: data.review,
    };
  }

  if (res.status === 202 || res.ok) {
    return { mode: "async", alreadyRunning: Boolean(data.alreadyRunning) };
  }

  // After budget (etc.) completes, Celery still holds that job lock while it
  // chains senior-editor. A naive POST for the next phase gets 409 — treat it
  // as alreadyRunning and wait, instead of aborting Generate.
  if (res.status === 409) {
    const detail = formatApiDetail(data.detail, "");
    const runningPhase = asGeneratePipelinePhase(
      parseAlreadyRunningJobType(detail)
    );
    const requestedPhase = asGeneratePipelinePhase(options?.phase ?? null);
    if (runningPhase && requestedPhase) {
      return { mode: "async", alreadyRunning: true };
    }
    throwIfProposalStopped(res, { detail });
  }

  throwIfProposalStopped(res, data);
  throw new Error(data.detail ?? "Failed to start proposal phase");
}

async function fetchProposalJobStatus(
  rfpId: string
): Promise<ProposalJobStatus | null> {
  try {
    const res = await fetchWithTimeout(
      `/api/rfps/${rfpId}/proposal/job-status`,
      { cache: "no-store" },
      15_000
    );
    if (!res.ok) return null;
    const data = (await res.json()) as { job?: ProposalJobStatus | null };
    return data.job ?? null;
  } catch {
    return null;
  }
}

function isPhaseFinishedOnSnapshot(
  snapshot: {
    draft: ProposalOutline | null;
    research: ProposalResearch | null;
    pipelineStatus: ProposalPipelineStatus | null;
  },
  phase: PipelinePhase
): boolean {
  const fromServer = phaseCompleteOnServer(snapshot.pipelineStatus, phase);
  if (fromServer !== null) return fromServer;
  return phaseIsComplete(snapshot.draft, snapshot.research, phase);
}

/**
 * After a 202 start, poll draft/checkpoint (and optional in-memory job status)
 * until the phase finishes. Never holds a long POST open through the proxy.
 */
async function waitForProposalPhase(
  rfpId: string,
  phase: PipelinePhase,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  const deadline = Date.now() + STAGE_POLL_MAX_MS;
  let observedRunning = false;
  const startedWall = Date.now();

  while (Date.now() < deadline) {
    throwIfAborted(signal);

    const snapshot = await fetchProposalDraft(rfpId);
    const cp = snapshot.research?.pipelineCheckpoint;
    const job = await fetchProposalJobStatus(rfpId);

    const inProg = asGeneratePipelinePhase(cp?.inProgressPhase);
    const lastDone = asGeneratePipelinePhase(cp?.lastCompletedPhase);
    const jobPhase = asGeneratePipelinePhase(job?.jobType ?? null);

    if (cp?.inProgressPhase === phase) {
      observedRunning = true;
    }
    if (job?.jobType === phase && job.status === "running") {
      observedRunning = true;
    }
    // Predecessor still finishing (Celery about to chain to us).
    if (inProg && phaseIndex(inProg) < phaseIndex(phase)) {
      observedRunning = true;
    }
    if (
      jobPhase &&
      job?.status === "running" &&
      phaseIndex(jobPhase) < phaseIndex(phase)
    ) {
      observedRunning = true;
    }

    if (job?.jobType === phase) {
      if (job.status === "cancelled") {
        throw new DOMException(
          job.error ?? "Proposal generation stopped",
          "AbortError"
        );
      }
      if (job.status === "failed") {
        throw new Error(job.error ?? `${phase} failed`);
      }
    }

    // Checkpoint already advanced past this phase (server chain or prior run).
    if (lastDone && phaseIndex(lastDone) >= phaseIndex(phase) && inProg !== phase) {
      return { draft: snapshot.draft, research: snapshot.research };
    }
    // Successor already running — Celery chained past us.
    if (inProg && phaseIndex(inProg) > phaseIndex(phase)) {
      return { draft: snapshot.draft, research: snapshot.research };
    }
    if (
      jobPhase &&
      job?.status === "running" &&
      phaseIndex(jobPhase) > phaseIndex(phase)
    ) {
      return { draft: snapshot.draft, research: snapshot.research };
    }

    if (observedRunning && cp?.inProgressPhase !== phase) {
      if (cp?.lastFailedPhase === phase) {
        const err = cp.lastError ?? `${phase} failed`;
        if (/stopped|cancel/i.test(err)) {
          throw new DOMException(err, "AbortError");
        }
        throw new Error(err);
      }
      // Still on an earlier generate phase — keep waiting for the chain.
      if (inProg && phaseIndex(inProg) < phaseIndex(phase)) {
        await sleep(STAGE_POLL_INTERVAL_MS);
        continue;
      }
      if (
        jobPhase &&
        job?.status === "running" &&
        phaseIndex(jobPhase) < phaseIndex(phase)
      ) {
        await sleep(STAGE_POLL_INTERVAL_MS);
        continue;
      }
      if (
        cp?.lastCompletedPhase === phase ||
        isPhaseFinishedOnSnapshot(snapshot, phase)
      ) {
        return { draft: snapshot.draft, research: snapshot.research };
      }
      if (job?.jobType === phase && job.status === "completed") {
        return { draft: snapshot.draft, research: snapshot.research };
      }
    }

    // Job finished in memory before we observed in-progress (fast phase).
    if (
      !observedRunning &&
      job?.jobType === phase &&
      job.status === "completed" &&
      isPhaseFinishedOnSnapshot(snapshot, phase)
    ) {
      return { draft: snapshot.draft, research: snapshot.research };
    }

    // Started but checkpoint not visible yet — don't treat as failure.
    if (!observedRunning && Date.now() - startedWall > 90_000) {
      if (isPhaseFinishedOnSnapshot(snapshot, phase)) {
        return { draft: snapshot.draft, research: snapshot.research };
      }
      throw new Error(
        `Timed out waiting for ${phase} to start. Check that the backend is running.`
      );
    }

    await sleep(STAGE_POLL_INTERVAL_MS);
  }

  throw new Error(`Timed out waiting for ${phase} to finish.`);
}

const FULFILL_POLL_MAX_MS = 90 * 60 * 1000;

async function waitForFulfillScan(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  const deadline = Date.now() + FULFILL_POLL_MAX_MS;
  let observedRunning = false;
  const startedWall = Date.now();

  while (Date.now() < deadline) {
    throwIfAborted(signal);

    const snapshot = await fetchProposalDraft(rfpId);
    const cp = snapshot.research?.pipelineCheckpoint;
    const job = await fetchProposalJobStatus(rfpId);

    if (cp?.inProgressPhase === FULFILL_SCAN_PHASE) {
      observedRunning = true;
    }
    if (job?.jobType === FULFILL_SCAN_PHASE && job.status === "running") {
      observedRunning = true;
    }

    if (job?.jobType === FULFILL_SCAN_PHASE) {
      if (job.status === "cancelled") {
        throw new DOMException(
          job.error ?? "Proposal generation stopped",
          "AbortError"
        );
      }
      if (job.status === "failed") {
        throw new Error(job.error ?? "Complete & clean failed");
      }
      if (job.status === "completed" && cp?.inProgressPhase !== FULFILL_SCAN_PHASE) {
        return { draft: snapshot.draft, research: snapshot.research };
      }
    }

    if (observedRunning && cp?.inProgressPhase !== FULFILL_SCAN_PHASE) {
      // record_generation_stopped's fulfill-scan branch (backend) never
      // stamps lastFailedPhase with "fulfill-scan" — it only carries the
      // prior value forward — so checking lastFailedPhase alone missed a
      // stop whenever the job-status check above didn't already catch it
      // first (e.g. job entry cleared/replaced), and a stopped run got
      // treated as a successful one. lastError is the field the stop path
      // actually always sets, so check that first regardless of phase name.
      if (cp?.lastError && /stopped|cancel/i.test(cp.lastError)) {
        throw new DOMException(cp.lastError, "AbortError");
      }
      if (cp?.lastFailedPhase === FULFILL_SCAN_PHASE) {
        throw new Error(cp.lastError ?? "Complete & clean failed");
      }
      return { draft: snapshot.draft, research: snapshot.research };
    }

    if (!observedRunning && Date.now() - startedWall > 90_000) {
      if (snapshot.draft?.lastFulfillReport) {
        return { draft: snapshot.draft, research: snapshot.research };
      }
      throw new Error(
        "Timed out waiting for Complete & clean to start. Check that the backend is running."
      );
    }

    await sleep(STAGE_POLL_INTERVAL_MS);
  }

  throw new Error("Timed out waiting for Complete & clean to finish.");
}

/** Poll until an in-flight Complete & clean job finishes (no POST — reconnect only). */
export async function pollFulfillScanCompletion(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  return waitForFulfillScan(rfpId, signal);
}

async function runProposalPhaseAsync(
  rfpId: string,
  phase: PipelinePhase,
  path: string,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
  budget?: ProposalBudget;
  review?: PreSubmitReview;
}> {
  const started = await startProposalPhaseJob(path, signal, { phase });
  if (started.mode === "sync") {
    return {
      draft: started.draft ? apiDraftToOutline(started.draft) : null,
      research: started.research ?? null,
      budget: started.budget,
      review: started.review,
    };
  }
  const waited = await waitForProposalPhase(rfpId, phase, signal);
  return waited;
}

/** Soft client ceilings — 0 means wait (no AbortSignal). Override via env if needed. */
const PROPOSAL_FETCH_TIMEOUT_MS = 0;
/** First paint — allow slow Supabase/proxy without aborting the workspace shell. */
export const PROPOSAL_INITIAL_LOAD_TIMEOUT_MS = 0;

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = PROPOSAL_FETCH_TIMEOUT_MS
): Promise<Response> {
  if (!timeoutMs || timeoutMs <= 0) {
    return fetch(input, init);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchProposalSnapshot(
  rfpId: string,
  savedAt: string
): Promise<NonNullable<ProposalOutline["snapshots"]>[number] | null> {
  const qs = new URLSearchParams({ savedAt });
  // Query param (not path) — ISO offsets with '+' break in path segments.
  const res = await fetchWithTimeout(
    `/api/rfps/${rfpId}/proposal/snapshot?${qs.toString()}`,
    { cache: "no-store" },
    120_000
  );
  if (!res.ok) return null;
  const data = (await res.json()) as {
    snapshot?: NonNullable<ProposalOutline["snapshots"]>[number];
  };
  return data.snapshot ?? null;
}

async function captureProposalTimestamps(rfpId: string): Promise<{
  draftAt: string;
  researchAt: string;
}> {
  const { draft, research } = await fetchProposalDraft(rfpId);
  return {
    draftAt: draft?.updatedAt ?? "1970-01-01T00:00:00.000Z",
    researchAt: research?.updatedAt ?? "1970-01-01T00:00:00.000Z",
  };
}

/** Wait for backend to finish saving when the HTTP proxy dropped early. */
async function pollForBackendStageSave(
  rfpId: string,
  startedAt: { draftAt: string; researchAt: string },
  options?: { minSections?: number; requireBudget?: boolean; requireStaticSections1to3?: boolean }
): Promise<{ draft: ProposalOutline; research: ProposalResearch } | null> {
  const minSections = options?.minSections ?? 10;
  const deadline = Date.now() + STAGE_POLL_MAX_MS;
  let lastResearchAt = "";
  let stablePolls = 0;

  const sectionsReady = (draft: ProposalOutline) => {
    if (options?.requireStaticSections1to3) {
      return staticSections1to3Complete(draft);
    }
    return countSectionsWithContent(draft) >= minSections;
  };

  while (Date.now() < deadline) {
    await sleep(STAGE_POLL_INTERVAL_MS);
    const snapshot = await fetchProposalDraft(rfpId);
    const { draft, research } = snapshot;
    if (!draft || !research || !sectionsReady(draft)) {
      continue;
    }
    if (options?.requireBudget && !research.budget) {
      continue;
    }

    const advanced =
      draft.updatedAt > startedAt.draftAt ||
      research.updatedAt > startedAt.researchAt;
    if (!advanced) {
      continue;
    }

    if (research.updatedAt === lastResearchAt) {
      stablePolls += 1;
      if (stablePolls >= 3) {
        return { draft, research };
      }
    } else {
      lastResearchAt = research.updatedAt;
      stablePolls = 0;
    }
  }

  const finalSnapshot = await fetchProposalDraft(rfpId);
  if (
    finalSnapshot.draft &&
    finalSnapshot.research &&
    sectionsReady(finalSnapshot.draft) &&
    (!options?.requireBudget || finalSnapshot.research.budget)
  ) {
    return {
      draft: finalSnapshot.draft,
      research: finalSnapshot.research,
    };
  }
  return null;
}

/**
 * Run the full pipeline as shorter requests so no single HTTP call
 * must stay open for the entire multi-batch Phase 3 run.
 * Skips phases already completed unless forceRestart is set.
 */
export async function generateFullProposalStaged(
  rfpId: string,
  onProgress?: (stage: FullProposalProgress) => void,
  options?: {
    startFrom?: PipelinePhase;
    forceRestart?: boolean;
    /** Re-run startFrom and every later phase even if previously complete. */
    forceRerunFromStart?: boolean;
    onDraftUpdate?: (draft: ProposalOutline) => void;
    onResearchUpdate?: (research: ProposalResearch | null) => void;
    signal?: AbortSignal;
  }
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const signal = options?.signal;
  try {
  throwIfAborted(signal);
  await clearProposalGenerationStop(rfpId);

  // Soft restart: do NOT wipe the DB. Backend archives + regenerates in place.
  // (Hard wipe is only Reset draft.) Clearing happens in the UI only.
  const snapshot = await fetchProposalDraft(rfpId);
  let draft = snapshot.draft;
  let research = snapshot.research;
  let pipelineStatus = snapshot.pipelineStatus;

  // Resume ownership is backend: trust server pipelineStatus.resumeFromPhase.
  // Client may only force a known restart entrypoint (Sections 1–3 / Phase 2).
  let resumeFrom: PipelinePhase;
  if (options?.forceRestart) {
    resumeFrom = "sections-1-3";
  } else if (options?.forceRerunFromStart && options.startFrom) {
    resumeFrom = options.startFrom;
  } else if (options?.startFrom && options.startFrom === "phase-2") {
    resumeFrom = "phase-2";
  } else {
    resumeFrom =
      pipelineStatus?.resumeFromPhase ??
      research?.pipelineCheckpoint?.resumeFromPhase ??
      "sections-1-3";
  }

  // Keep incomplete 1–3 from blocking an explicit Intelligence+ start.
  const skipStaticGate =
    resumeFrom === "phase-2" || options?.forceRerunFromStart === true;
  if (draft && !staticSections1to3Complete(draft) && !skipStaticGate) {
    resumeFrom = "sections-1-3";
  }

  const run = (phase: PipelinePhase) =>
    options?.forceRestart || shouldRunPhase(phase, resumeFrom);
  let lastRanPhaseThisInvocation: PipelinePhase | null = null;

  async function skipIfPhaseAlreadyFinished(phase: PipelinePhase): Promise<boolean> {
    throwIfAborted(signal);
    if (options?.forceRestart) return false;
    // "Start after Sections 1–3" must re-run Phase 2+ even when prior runs completed.
    if (
      options?.forceRerunFromStart &&
      options.startFrom &&
      shouldRunPhase(phase, options.startFrom)
    ) {
      return false;
    }
    await waitForInFlightPhase(rfpId, phase, onProgress);
    const snap = await fetchProposalDraft(rfpId);
    draft = snap.draft ?? draft;
    research = snap.research ?? research;
    pipelineStatus = snap.pipelineStatus ?? pipelineStatus;
    const fromServer = phaseCompleteOnServer(pipelineStatus, phase);
    return shouldSkipCompletedPhase({
      phase,
      lastRanThisInvocation: lastRanPhaseThisInvocation,
      lastCompletedPhase: research?.pipelineCheckpoint?.lastCompletedPhase ?? null,
      completedOnServer: fromServer,
      locallyComplete: phaseIsComplete(draft, research, phase),
    });
  }

  if (run("sections-1-3")) {
    if (!(await skipIfPhaseAlreadyFinished("sections-1-3"))) {
      throwIfAborted(signal);
      onProgress?.("sections-1-3");
      draft = await withLiveDraftPolling(
        rfpId,
        options?.onDraftUpdate,
        () =>
          generateProposalSections1to3(rfpId, signal, {
            // Full restart must rebuild 1–3. Case-studies / resume keep complete
            // Company + Bios and only fill missing Our Work.
            forceRegenerate: Boolean(options?.forceRestart),
          }),
        options?.onResearchUpdate
      );
      ({ draft, research, pipelineStatus } = await refreshProposalSnapshot(rfpId));
      lastRanPhaseThisInvocation = "sections-1-3";
    }
  }

  if (run("phase-2")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-2"))) {
      throwIfAborted(signal);
      onProgress?.("phase-2");
      research = await runPhase2Retrieval(rfpId, signal);
      ({ draft, research, pipelineStatus } = await refreshProposalSnapshot(rfpId));
      // Seed RFP tabs from fresh Intelligence so the sidebar updates before Phase 3.
      if (draft && research?.rfpSections?.length) {
        const isStatic = (id: string) =>
          id.startsWith("section-1-") ||
          id.startsWith("section-2-") ||
          id.startsWith("section-3-");
        const rebuilt: ProposalOutline = {
          ...draft,
          sections: [
            ...draft.sections.filter((s) => isStatic(s.id)),
            ...research.rfpSections
              .filter((mapped) => !isStatic(mapped.id))
              .map((mapped) => ({
                id: mapped.id,
                title: mapped.title,
                pageLimit: mapped.pageLimit ?? undefined,
                wordTarget: mapped.pageLimit
                  ? Math.max(300, mapped.pageLimit * 350)
                  : 800,
                required: true,
                custom: false,
                content: "",
                status: "outline" as const,
                source: "rfp" as const,
                mode: mapped.zoMode ?? ("write" as const),
              })),
          ],
          updatedAt: new Date().toISOString(),
        };
        draft = rebuilt;
        await saveProposalDraft(rfpId, draft);
        options?.onDraftUpdate?.(draft);
        options?.onResearchUpdate?.(research);
      }
      lastRanPhaseThisInvocation = "phase-2";
    }
  }

  if (run("phase-3")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3");
      const phase3 = await withLiveDraftPolling(
        rfpId,
        options?.onDraftUpdate,
        () => runPhase3Drafting(rfpId, signal),
        options?.onResearchUpdate
      );
      draft = phase3.draft;
      research = phase3.research;
      lastRanPhaseThisInvocation = "phase-3";
    }
  }

  if (run("phase-3-5-budget")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3-5-budget"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3-5-budget");
      const budgeted = await runPhase3_5BudgetWithRecovery(rfpId, signal);
      research = budgeted.research;
      if (budgeted.draft) draft = budgeted.draft;
      lastRanPhaseThisInvocation = "phase-3-5-budget";
    }
  }

  if (run("phase-3-6-self-edit")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3-6-self-edit"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3-6-self-edit");
      const edited = await withLiveDraftPolling(
        rfpId,
        options?.onDraftUpdate,
        () => runPhase3_6SelfEditWithRecovery(rfpId, signal),
        options?.onResearchUpdate
      );
      draft = edited.draft;
      research = edited.research;
      lastRanPhaseThisInvocation = "phase-3-6-self-edit";
    }
  }

  if (run("phase-4-review")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-4-review"))) {
      throwIfAborted(signal);
      onProgress?.("phase-4-review");
      const reviewed = await runPhase4PreSubmitReview(rfpId, signal);
      research = reviewed.research;
      lastRanPhaseThisInvocation = "phase-4-review";
    }
  }

  throwIfAborted(signal);

  ({ draft, research, pipelineStatus } = await refreshProposalSnapshot(rfpId));
  if (!draft || !research) {
    throw new Error("Proposal draft missing after pipeline run.");
  }

  // Stale isComplete from a prior run must not exit after this click re-ran
  // an earlier phase (e.g. Intelligence) while RFP tabs are still empty.
  if (
    (pipelineStatus?.isComplete || pipelineStatus?.resumeFromPhase === "complete") &&
    !lastRanPhaseThisInvocation
  ) {
    return { draft, research };
  }

  const blocker = validateStagedProposalComplete(draft, research);
  if (blocker) {
    const lower = blocker.toLowerCase();
    // VERIFY tags are a manual handoff after senior editor — do not soft-succeed
    // just because stale budget/review artifacts exist; only exit cleanly when the
    // server already considers the pipeline complete (handled above).
    if (lower.includes("verify") && !options?.forceRestart) {
      throwIfAborted(signal);
      onProgress?.("phase-3-6-self-edit");
      const retry = await runPhase3_6SelfEditWithRecovery(rfpId, signal);
      draft = retry.draft;
      research = retry.research;
      ({ draft, research, pipelineStatus } = await refreshProposalSnapshot(rfpId));
      if (
        pipelineStatus?.isComplete ||
        pipelineStatus?.resumeFromPhase === "complete"
      ) {
        return { draft: draft!, research: research! };
      }
      const retryBlocker =
        draft && research ? validateStagedProposalComplete(draft, research) : blocker;
      if (!retryBlocker || retryBlocker.toLowerCase().includes("verify")) {
        return { draft: draft!, research: research! };
      }
      throw new Error(retryBlocker);
    }
    throw new Error(blocker);
  }

  return { draft, research };
  } finally {
    if (signal?.aborted) {
      try {
        await stopProposalGeneration(rfpId);
      } catch {
        // Best-effort — UI also calls stop explicitly.
      }
    }
  }
}

async function refreshProposalSnapshot(rfpId: string): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
  pipelineStatus: ProposalPipelineStatus | null;
}> {
  const snapshot = await fetchProposalDraft(rfpId);
  return {
    draft: snapshot.draft,
    research: snapshot.research,
    pipelineStatus: snapshot.pipelineStatus,
  };
}

/** If backend is still finishing a phase after a proxy timeout, wait before re-firing it. */
async function waitForInFlightPhase(
  rfpId: string,
  phase: PipelinePhase,
  onProgress?: (stage: FullProposalProgress) => void
): Promise<void> {
  const snap = await fetchProposalDraft(rfpId);
  const cp = snap.research?.pipelineCheckpoint;
  const inFlight =
    cp?.inProgressPhase === phase ||
    (cp?.lastFailedPhase === phase && cp?.lastError?.toLowerCase().includes("timeout"));
  if (!inFlight) return;

  onProgress?.("recovering");
  const startedAt = await captureProposalTimestamps(rfpId);
  const staleDeadline = Date.now() + IN_FLIGHT_STALE_MS;

  while (Date.now() < staleDeadline) {
    await sleep(STAGE_POLL_INTERVAL_MS);
    const snapshot = await fetchProposalDraft(rfpId);
    const currentCp = snapshot.research?.pipelineCheckpoint;
    if (currentCp?.inProgressPhase !== phase) {
      return;
    }
    const { draft, research } = snapshot;
    if (!draft || !research) continue;
    const advanced =
      draft.updatedAt > startedAt.draftAt ||
      research.updatedAt > startedAt.researchAt;
    if (advanced) {
      await pollForBackendStageSave(rfpId, startedAt, {
        minSections: phase === "sections-1-3" ? 3 : 10,
        requireStaticSections1to3: phase === "sections-1-3",
        requireBudget: phase === "phase-3-5-budget",
      });
      return;
    }
  }
  // Stale in-progress checkpoint (uvicorn killed, browser disconnected) — start fresh.
}

export async function fetchProposalDraft(
  rfpId: string,
  options?: { timeoutMs?: number }
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
  provider?: string | null;
  pipelineStatus: ProposalPipelineStatus | null;
}> {
  const timeoutMs = options?.timeoutMs ?? PROPOSAL_FETCH_TIMEOUT_MS;
  const empty = {
    draft: null as ProposalOutline | null,
    research: null as ProposalResearch | null,
    pipelineStatus: null as ProposalPipelineStatus | null,
  };

  for (let attempt = 0; attempt < 4; attempt++) {
    let res: Response;
    try {
      res = await fetchWithTimeout(
        `/api/rfps/${rfpId}/proposal`,
        { cache: "no-store" },
        timeoutMs
      );
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        // During generation the single-worker backend may be busy — return empty
        // instead of crashing the page. The live polling will retry.
        if (attempt < 3) {
          await sleep(2000 * (attempt + 1));
          continue;
        }
        return empty;
      }
      throw error;
    }
    if (res.status === 503 || res.status === 502) {
      if (attempt < 3) {
        await sleep(500 * (attempt + 1));
        continue;
      }
      return empty;
    }
    if (!res.ok) {
      return empty;
    }
    const data = (await res.json()) as {
      draft: ApiProposalDraft | null;
      research: ProposalResearch | null;
      pipelineStatus?: ProposalPipelineStatus | null;
    };
    const researchRaw = data.research ?? null;
    const draftPayload = data.draft;
    if (!draftPayload) {
      const research = normalizeCheckpointForDisplay(null, researchRaw);
      return {
        draft: null,
        research,
        pipelineStatus: buildPipelineStatus(
          null,
          research,
          data.pipelineStatus ?? null
        ),
      };
    }
    const draft = apiDraftToOutline(draftPayload);
    const research = normalizeCheckpointForDisplay(draft, researchRaw);
    return {
      draft,
      research,
      provider: draftPayload.provider,
      pipelineStatus: buildPipelineStatus(
        draft,
        research,
        data.pipelineStatus ?? null
      ),
    };
  }

  return empty;
}

export async function saveProposalDraft(
  rfpId: string,
  outline: ProposalOutline
): Promise<void> {
  await fetch(`/api/rfps/${rfpId}/proposal`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(outlineToApiDraft(rfpId, outline)),
  });
}

export async function generateFullProposalWithResearch(
  rfpId: string
): Promise<{ draft: ProposalOutline; research: ProposalResearch | null }> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/generate/full`,
    proposalPostInit()
  );
  const text = await res.text();
  let data: {
    detail?: string;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error(
      "Invalid response from server (full proposal may have timed out)."
    );
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Full proposal generation failed");
  }
  if (!data.draft) {
    throw new Error("No draft returned from server");
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
  };
}

export async function generateFullProposal(rfpId: string): Promise<ProposalOutline> {
  const { draft } = await generateFullProposalWithResearch(rfpId);
  return draft;
}

export async function generateProposalDraft(rfpId: string): Promise<ProposalOutline> {
  return generateFullProposal(rfpId);
}

/** Hard-wipe draft, research cache, and pipeline checkpoint on the server. */
export async function resetProposal(rfpId: string): Promise<void> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/reset`, {
    method: "POST",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = "Proposal reset failed";
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      // keep default
    }
    throw new Error(detail);
  }
}

/** Keep Sections 1–3; delete Intelligence / RFP tabs / budget / review for a clean Phase 2 rebuild. */
export async function restartProposalFromIntelligence(
  rfpId: string
): Promise<ProposalOutline> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/restart-from-intelligence`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    }
  );
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft } = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from restart-from-intelligence.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Failed to clear Intelligence for restart");
  }
  if (!data.draft) {
    throw new Error("No draft returned after clearing Intelligence");
  }
  return apiDraftToOutline(data.draft);
}

/**
 * Keep Company + Team Bios; strip Our Work case studies + Intelligence onward
 * so the next generate re-runs case-study extraction with current fit rules.
 */
export async function restartProposalFromCaseStudies(
  rfpId: string
): Promise<ProposalOutline> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/restart-from-case-studies`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    }
  );
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft } = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from restart-from-case-studies.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Failed to clear case studies for restart");
  }
  if (!data.draft) {
    throw new Error("No draft returned after clearing case studies");
  }
  return apiDraftToOutline(data.draft);
}

export interface CaseStudyMatchGap {
  capability: string;
  gapReason: string;
  closestTitle?: string;
  closestDisplayName?: string;
  closestScore?: number;
  closestExcerpt?: string;
}

export interface CaseStudyMatchStudy {
  title: string;
  displayName?: string;
  fitScore: number;
  fitLabel: string;
  capability: string;
  excerpt: string;
  matchedTerms: string[];
}

export interface CaseStudyMatchResult {
  ok: boolean;
  rfpId: string;
  capabilities: string[];
  selectedTitles: string[];
  studies: CaseStudyMatchStudy[];
  gaps: CaseStudyMatchGap[];
  matchQuality?: "strong" | "closest" | "none";
  prefetchedAt?: string | null;
  message: string;
}

export async function matchCaseStudiesForRfp(
  rfpId: string
): Promise<CaseStudyMatchResult> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/match-case-studies`, {
    method: "POST",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const text = await res.text();
  let data: { detail?: string } & Partial<CaseStudyMatchResult> = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from match-case-studies.");
  }
  if (!res.ok) {
    throw new Error(
      formatApiDetail(data.detail, "Failed to match case studies")
    );
  }
  return {
    ok: data.ok ?? true,
    rfpId: data.rfpId ?? rfpId,
    capabilities: data.capabilities ?? [],
    selectedTitles: data.selectedTitles ?? [],
    studies: data.studies ?? [],
    gaps: data.gaps ?? [],
    matchQuality: data.matchQuality,
    prefetchedAt: data.prefetchedAt,
    message: data.message ?? "",
  };
}

export async function generateProposalSections1to3(
  rfpId: string,
  signal?: AbortSignal,
  options?: { forceRegenerate?: boolean }
): Promise<ProposalOutline> {
  const forceRegenerate = options?.forceRegenerate !== false;
  const qs = new URLSearchParams({
    force_regenerate: forceRegenerate ? "true" : "false",
  });
  const result = await runProposalPhaseAsync(
    rfpId,
    "sections-1-3",
    `/api/rfps/${rfpId}/proposal/generate/sections-1-3?${qs.toString()}`,
    signal
  );
  if (!result.draft) {
    throw new Error("No draft returned after Sections 1–3 generation");
  }
  return result.draft;
}

export async function runPhase2Retrieval(
  rfpId: string,
  signal?: AbortSignal
): Promise<ProposalResearch> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-2",
    `/api/rfps/${rfpId}/proposal/phase-2-retrieval`,
    signal
  );
  if (!result.research) {
    throw new Error("No research data returned after Phase 2");
  }
  return result.research;
}

export async function runPhase3Drafting(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-3",
    `/api/rfps/${rfpId}/proposal/phase-3-drafting`,
    signal
  );
  if (!result.draft || !result.research) {
    throw new Error("No draft or research returned after Phase 3");
  }
  return { draft: result.draft, research: result.research };
}

export async function runDesignerCompactManuscript(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-3-6-self-edit",
    `/api/rfps/${rfpId}/proposal/designer-compact`,
    signal
  );
  if (!result.draft || !result.research) {
    throw new Error("No draft returned from designer-compact");
  }
  return { draft: result.draft, research: result.research };
}

export async function runPhase3_6SelfEdit(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-3-6-self-edit",
    `/api/rfps/${rfpId}/proposal/phase-3-6-self-edit`,
    signal
  );
  if (!result.draft || !result.research) {
    throw new Error("No draft returned from self-edit");
  }
  return { draft: result.draft, research: result.research };
}

export async function runPhase3_5Budget(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-3-5-budget",
    `/api/rfps/${rfpId}/proposal/phase-3-5-budget`,
    signal
  );
  if (!result.research?.budget) {
    throw new Error("No budget data returned after Phase 3.5");
  }
  return {
    budget: result.research.budget,
    research: result.research,
    draft: result.draft,
  };
}

export async function runPhase3_5BudgetReconcile(
  rfpId: string
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/phase-3-5-budget-reconcile`,
    { ...proposalPostInit() }
  );
  const text = await res.text();
  let data: {
    detail?: string;
    budget?: ProposalBudget;
    research?: ProposalResearch;
    draft?: Parameters<typeof apiDraftToOutline>[0] | null;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (budget reconcile failed).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Budget reconcile failed");
  }
  if (!data.budget || !data.research) {
    throw new Error("No budget data returned from reconcile");
  }
  return {
    budget: data.budget,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
  };
}

export async function runPhase3_5BudgetReconcileWithRecovery(
  rfpId: string
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
  recoveredFromDraft: boolean;
}> {
  const startedAt = await captureProposalTimestamps(rfpId);
  try {
    const result = await runPhase3_5BudgetReconcile(rfpId);
    return { ...result, recoveredFromDraft: false };
  } catch (error) {
    const polled = await pollForBackendStageSave(rfpId, startedAt, {
      requireBudget: true,
    });
    if (polled?.research.budget) {
      return {
        budget: polled.research.budget,
        research: polled.research,
        draft: polled.draft,
        recoveredFromDraft: true,
      };
    }
    const recovered = await fetchProposalDraft(rfpId);
    if (recovered.research?.budget && recovered.draft) {
      return {
        budget: recovered.research.budget,
        research: recovered.research,
        draft: recovered.draft,
        recoveredFromDraft: true,
      };
    }
    throw error;
  }
}

export async function generateProposalPricing(
  rfpId: string
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-3-5-budget",
    `/api/rfps/${rfpId}/proposal/pricing/generate`
  );
  if (!result.research?.budget) {
    throw new Error("No budget data returned from server");
  }
  return {
    budget: result.research.budget,
    research: result.research,
    draft: result.draft,
  };
}

export async function runPhase4PreSubmitReview(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ review: PreSubmitReview; research: ProposalResearch }> {
  const result = await runProposalPhaseAsync(
    rfpId,
    "phase-4-review",
    `/api/rfps/${rfpId}/proposal/phase-4-review`,
    signal
  );
  if (!result.research?.presubmitReview) {
    throw new Error("No review data returned after Phase 4");
  }
  return {
    review: result.research.presubmitReview,
    research: result.research,
  };
}

export async function runPhase4FinalizeGaps(
  rfpId: string
): Promise<{
  review: PreSubmitReview;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-4-finalize-gaps`, {
    ...proposalPostInit(),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    review?: PreSubmitReview;
    research?: ProposalResearch;
    draft?: ApiProposalDraft | null;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from finalize gaps.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Finalize gaps failed");
  }
  if (!data.review || !data.research) {
    throw new Error("No finalize gaps data returned");
  }
  return {
    review: data.review,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
  };
}

export async function runFulfillRfpGaps(
  rfpId: string,
  options?: { useLlm?: boolean; mode?: string; signal?: AbortSignal }
): Promise<{
  review: PreSubmitReview;
  research: ProposalResearch;
  draft: ProposalOutline;
  fulfillReport: {
    mode?: string;
    sectionsScanned?: number;
    verifyTagsRemoved?: number;
    verifyTagsKept?: number;
    closingDetected?: string[];
    closingDetectedSections?: Array<{ id: string; title: string }>;
    closingAlreadyPresent?: Array<{ id: string; title: string }>;
    inPlaceFixCount?: number;
    closingAdded?: string[];
    closingAddedSections?: Array<{ id: string; title: string }>;
    submissionNarrativesAdded?: string[];
    submissionDeliverablesAdded?: Array<{ id: string; title: string; kind?: string }>;
    logs?: string[];
    humanDecisionGaps?: string[];
    ledgerAdditionsApplied?: number;
    ledgerAdditionsSectionTitles?: string[];
    ledgerMergesApplied?: number;
    ledgerMergesSectionTitles?: string[];
    ledgerCutsApplied?: number;
    ledgerCutsSectionTitles?: string[];
    truncatedSectionsCount?: number;
    truncatedSectionTitles?: string[];
    unverifiedClaimsCount?: number;
  };
}> {
  const body = JSON.stringify({
    useLlm: options?.useLlm ?? true,
    mode: options?.mode ?? "full",
  });
  throwIfAborted(options?.signal);
  await clearProposalGenerationStop(rfpId);
  const started = await startProposalPhaseJob(
    `/api/rfps/${rfpId}/proposal/fulfill-rfp-gaps`,
    options?.signal,
    { body }
  );
  if (started.mode === "sync") {
    if (!started.review || !started.research || !started.draft) {
      throw new Error("Incomplete fulfill RFP gaps response");
    }
    return {
      review: started.review,
      research: started.research,
      draft: apiDraftToOutline(started.draft),
      fulfillReport: {},
    };
  }
  const waited = await waitForFulfillScan(rfpId, options?.signal);
  if (!waited.draft || !waited.research) {
    throw new Error("Incomplete fulfill RFP gaps response");
  }
  const review: PreSubmitReview = waited.research.presubmitReview ?? {
    rfpId,
    issues: [],
    complianceChecklist: [],
    summary: "",
    readyToSubmit: false,
    scannedAt: new Date().toISOString(),
  };
  const fulfillReport = (waited.draft.lastFulfillReport ?? {}) as {
    mode?: string;
    sectionsScanned?: number;
    verifyTagsRemoved?: number;
    verifyTagsKept?: number;
    closingDetected?: string[];
    closingDetectedSections?: Array<{ id: string; title: string }>;
    closingAlreadyPresent?: Array<{ id: string; title: string }>;
    inPlaceFixCount?: number;
    closingAdded?: string[];
    closingAddedSections?: Array<{ id: string; title: string }>;
    submissionNarrativesAdded?: string[];
    submissionDeliverablesAdded?: Array<{ id: string; title: string; kind?: string }>;
    logs?: string[];
    humanDecisionGaps?: string[];
    ledgerAdditionsApplied?: number;
    ledgerAdditionsSectionTitles?: string[];
    ledgerMergesApplied?: number;
    ledgerMergesSectionTitles?: string[];
    ledgerCutsApplied?: number;
    ledgerCutsSectionTitles?: string[];
    truncatedSectionsCount?: number;
    truncatedSectionTitles?: string[];
    unverifiedClaimsCount?: number;
  };
  return {
    review,
    research: waited.research,
    draft: waited.draft,
    fulfillReport,
  };
}

async function waitForNamedPhaseJob(
  rfpId: string,
  phase: string,
  signal: AbortSignal | undefined,
  options: { label: string; maxMs?: number }
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  const label = options.label;
  const maxMs = options.maxMs ?? 10 * 60 * 1000;
  const deadline = Date.now() + maxMs;
  let observedRunning = false;
  const startedWall = Date.now();

  while (Date.now() < deadline) {
    throwIfAborted(signal);
    const snapshot = await fetchProposalDraft(rfpId);
    const cp = snapshot.research?.pipelineCheckpoint;
    const job = await fetchProposalJobStatus(rfpId);

    if (cp?.inProgressPhase === phase) observedRunning = true;
    if (job?.jobType === phase && job.status === "running") observedRunning = true;

    if (job?.jobType === phase) {
      if (job.status === "cancelled") {
        throw new DOMException(
          job.error ?? "Proposal generation stopped",
          "AbortError"
        );
      }
      if (job.status === "failed") {
        throw new Error(job.error ?? `${label} failed`);
      }
      if (job.status === "completed" && cp?.inProgressPhase !== phase) {
        return { draft: snapshot.draft, research: snapshot.research };
      }
    }

    if (observedRunning && cp?.inProgressPhase !== phase) {
      if (cp?.lastError && /stopped|cancel/i.test(cp.lastError)) {
        throw new DOMException(cp.lastError, "AbortError");
      }
      if (cp?.lastFailedPhase === phase) {
        throw new Error(cp.lastError ?? `${label} failed`);
      }
      return { draft: snapshot.draft, research: snapshot.research };
    }

    if (!observedRunning && Date.now() - startedWall > 60_000) {
      throw new Error(
        `Timed out waiting for ${label} to start. Check that the backend is running.`
      );
    }

    await sleep(STAGE_POLL_INTERVAL_MS);
  }

  throw new Error(`Timed out waiting for ${label} to finish.`);
}

/** Reorder / stub sidebar tabs to RFP submission order — no prose rewrite. */
/** Poll until an in-flight Align to RFP outline job finishes (no POST — reconnect only). */
export async function pollAlignRfpOutlineCompletion(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  return waitForNamedPhaseJob(rfpId, ALIGN_RFP_OUTLINE_PHASE, signal, {
    label: "Align to RFP outline",
    maxMs: 10 * 60 * 1000,
  });
}

export type AlignOutlinePreview = {
  currentTitles?: string[];
  proposedTitles?: string[];
  rfpNeededTitles?: string[];
  changes?: string[];
  addedTitles?: string[];
  nothingToChange?: boolean;
  summary?: string;
  humanDecisionGaps?: string[];
};

export async function previewAlignRfpOutline(
  rfpId: string,
  options?: { signal?: AbortSignal }
): Promise<{
  ok: boolean;
  preview: AlignOutlinePreview;
  nothingToChange: boolean;
}> {
  throwIfAborted(options?.signal);
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/align-rfp-outline/preview`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      signal: options?.signal,
    }
  );
  const text = await res.text();
  let data: {
    detail?: string;
    ok?: boolean;
    preview?: AlignOutlinePreview;
    nothingToChange?: boolean;
  } = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from align preview.");
  }
  if (!res.ok) {
    throw new Error(formatApiDetail(data.detail, "Align preview failed"));
  }
  return {
    ok: Boolean(data.ok ?? true),
    preview: data.preview ?? {},
    nothingToChange: Boolean(
      data.nothingToChange ?? data.preview?.nothingToChange
    ),
  };
}

export async function runAlignRfpOutline(
  rfpId: string,
  options?: { signal?: AbortSignal }
): Promise<{
  draft: ProposalOutline;
  research: ProposalResearch | null;
  report: {
    mode?: string;
    beforeTitles?: string[];
    afterTitles?: string[];
    logs?: string[];
    summary?: string;
    changed?: boolean;
  };
}> {
  throwIfAborted(options?.signal);
  await clearProposalGenerationStop(rfpId);
  const started = await startProposalPhaseJob(
    `/api/rfps/${rfpId}/proposal/align-rfp-outline`,
    options?.signal
  );
  if (started.mode === "sync") {
    if (!started.draft) {
      throw new Error("Incomplete Align to RFP outline response");
    }
    const outline = apiDraftToOutline(started.draft);
    return {
      draft: outline,
      research: started.research ?? null,
      report: (outline.lastFulfillReport ?? {}) as {
        mode?: string;
        beforeTitles?: string[];
        afterTitles?: string[];
        logs?: string[];
        summary?: string;
        changed?: boolean;
      },
    };
  }
  const waited = await waitForNamedPhaseJob(
    rfpId,
    ALIGN_RFP_OUTLINE_PHASE,
    options?.signal,
    { label: "Align to RFP outline", maxMs: 10 * 60 * 1000 }
  );
  if (!waited.draft) {
    throw new Error("Incomplete Align to RFP outline response");
  }
  return {
    draft: waited.draft,
    research: waited.research,
    report: (waited.draft.lastFulfillReport ?? {}) as {
      mode?: string;
      beforeTitles?: string[];
      afterTitles?: string[];
      logs?: string[];
      summary?: string;
      changed?: boolean;
    },
  };
}

export type PacketRedistributeReport = {
  mode?: string;
  movedCount?: number;
  plannedMoves?: number;
  moveSummaries?: string[];
  skipped?: string[];
  humanGaps?: string[];
  stubFillIds?: string[];
  stubTitles?: string[];
  summary?: string;
  changed?: boolean;
  logs?: string[];
};

export type PacketPlacePreview = {
  issues?: string[];
  moves?: Array<{
    kind?: string;
    heading?: string;
    fromTitle?: string;
    toTitle?: string;
    summary?: string;
  }>;
  humanGaps?: string[];
  stubTitles?: string[];
  plannedMoves?: number;
  summary?: string;
  nothingToMove?: boolean;
};

export async function previewPacketRedistribute(
  rfpId: string,
  options?: { signal?: AbortSignal }
): Promise<{ ok: boolean; preview: PacketPlacePreview; plannedMoves: number }> {
  throwIfAborted(options?.signal);
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/packet-redistribute/preview`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      signal: options?.signal,
    }
  );
  const text = await res.text();
  let data: {
    detail?: string;
    ok?: boolean;
    preview?: PacketPlacePreview;
    plannedMoves?: number;
  } = {};
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from place preview.");
  }
  if (!res.ok) {
    throw new Error(formatApiDetail(data.detail, "Place preview failed"));
  }
  return {
    ok: Boolean(data.ok ?? true),
    preview: data.preview ?? {},
    plannedMoves: data.plannedMoves ?? data.preview?.plannedMoves ?? 0,
  };
}

export async function runPacketRedistribute(
  rfpId: string,
  options?: { signal?: AbortSignal }
): Promise<{
  draft: ProposalOutline;
  research: ProposalResearch | null;
  report: PacketRedistributeReport;
}> {
  throwIfAborted(options?.signal);
  await clearProposalGenerationStop(rfpId);
  const started = await startProposalPhaseJob(
    `/api/rfps/${rfpId}/proposal/packet-redistribute`,
    options?.signal
  );
  if (started.mode === "sync") {
    if (!started.draft) {
      throw new Error("Incomplete Place content response");
    }
    const outline = apiDraftToOutline(started.draft);
    return {
      draft: outline,
      research: started.research ?? null,
      report: (outline.lastFulfillReport ?? {}) as PacketRedistributeReport,
    };
  }
  const waited = await waitForNamedPhaseJob(
    rfpId,
    PACKET_REDISTRIBUTE_PHASE,
    options?.signal,
    { label: "Place content", maxMs: 15 * 60 * 1000 }
  );
  if (!waited.draft) {
    throw new Error("Incomplete Place content response");
  }
  return {
    draft: waited.draft,
    research: waited.research,
    report: (waited.draft.lastFulfillReport ?? {}) as PacketRedistributeReport,
  };
}

export async function restoreProposalSnapshot(
  rfpId: string,
  savedAt: string
): Promise<ProposalOutline> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/restore-snapshot`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ savedAt }),
  });
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from restore snapshot.");
  }
  if (!res.ok) {
    throw new Error(formatApiDetail(data.detail, "Could not restore snapshot."));
  }
  if (!data.draft) {
    throw new Error("Incomplete restore snapshot response.");
  }
  return apiDraftToOutline(data.draft);
}

export type ProposalDraftArchiveMeta = {
  id: string;
  rfpId: string;
  archivedAt: string;
  reason: string;
  label?: string | null;
  sectionCount: number;
  filledCount: number;
};

export async function listProposalArchives(
  rfpId: string
): Promise<ProposalDraftArchiveMeta[]> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/archives`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const text = await res.text();
  let data: { detail?: string; archives?: ProposalDraftArchiveMeta[] };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from proposal archives.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Could not list proposal archives.");
  }
  return data.archives ?? [];
}

export async function restoreProposalArchive(
  rfpId: string,
  archiveId: string
): Promise<ProposalOutline> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/archives/${encodeURIComponent(archiveId)}/restore`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    }
  );
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from restore archive.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Could not restore archive.");
  }
  if (!data.draft) {
    throw new Error("Incomplete restore archive response.");
  }
  return apiDraftToOutline(data.draft);
}

export async function runPhase4PreSubmitAutoFix(
  rfpId: string,
  options?: { useLlm?: boolean; signal?: AbortSignal }
): Promise<{
  review: PreSubmitReview;
  research: ProposalResearch;
  draft: ProposalOutline;
  autoFix: PreSubmitAutoFixReport;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-4-auto-fix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ useLlm: options?.useLlm ?? true }),
    signal: options?.signal,
  });
  const text = await res.text();
  let data: {
    detail?: string;
    review?: PreSubmitReview;
    research?: ProposalResearch;
    draft?: ApiProposalDraft;
    autoFix?: PreSubmitAutoFixReport;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from auto-fix.");
  }
  if (res.status === 499) {
    const err = new Error(data.detail ?? "Auto-fix stopped.");
    err.name = "AbortError";
    throw err;
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pre-submit auto-fix failed");
  }
  if (!data.review || !data.research || !data.draft || !data.autoFix) {
    throw new Error("Incomplete auto-fix response");
  }
  return {
    review: data.review,
    research: data.research,
    draft: apiDraftToOutline(data.draft),
    autoFix: data.autoFix,
  };
}

export async function improveProposalSection(
  rfpId: string,
  sectionId: string,
  message: string,
  options?: {
    selection?: { start: number; end: number; text: string };
    conversationHistory?: { role: "user" | "assistant"; content: string }[];
    proposalWide?: boolean;
    applyFix?: boolean;
    improveSectionPinned?: boolean;
  }
): Promise<{
  section: OutlineSection;
  draft: ProposalOutline;
  research: ProposalResearch | null;
  assistantMessage: string;
  draftChanged: boolean;
  suggestedFix: {
    sectionId: string;
    instruction: string;
    summary: string;
    sectionTitle?: string;
  } | null;
  agentActivity: {
    outcome: string;
    steps: string[];
    changes: string[];
    discrepancies: string[];
  } | null;
}> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/sections/${sectionId}/improve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        ...(options?.selection
          ? {
              selectionStart: options.selection.start,
              selectionEnd: options.selection.end,
              selectionText: options.selection.text.slice(0, 8000),
            }
          : {}),
        ...(options?.conversationHistory?.length
          ? {
              conversationHistory: options.conversationHistory
                .slice(-12)
                .map((turn) => ({
                  role: turn.role,
                  content:
                    turn.content.length > 11_900
                      ? `${turn.content.slice(0, 11_900)}\n…[truncated]`
                      : turn.content,
                })),
            }
          : {}),
        ...(options?.proposalWide ? { proposalWide: true } : {}),
        ...(options?.applyFix ? { applyFix: true } : {}),
        ...(options?.improveSectionPinned ? { improveSectionPinned: true } : {}),
      }),
    }
  );
  const text = await res.text();
  let data: {
    detail?: unknown;
    section?: OutlineSection;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
    assistantMessage?: string;
    draftChanged?: boolean;
    suggestedFix?: {
      sectionId?: string;
      instruction?: string;
      summary?: string;
      sectionTitle?: string;
    } | null;
    agentActivity?: {
      outcome?: string;
      steps?: string[];
      changes?: string[];
      discrepancies?: string[];
    } | null;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (section improve may have timed out).");
  }
  if (!res.ok) {
    throw new Error(formatApiDetail(data.detail, "Section improve failed"));
  }
  if (!data.section || !data.draft) {
    throw new Error("No section data returned from server");
  }
  const rawFix = data.suggestedFix;
  const suggestedFix =
    rawFix &&
    typeof rawFix.sectionId === "string" &&
    rawFix.sectionId &&
    typeof rawFix.instruction === "string" &&
    rawFix.instruction.trim()
      ? {
          sectionId: rawFix.sectionId,
          instruction: rawFix.instruction.trim(),
          summary: (rawFix.summary || rawFix.instruction).trim(),
          sectionTitle: rawFix.sectionTitle,
        }
      : null;
  const rawActivity = data.agentActivity;
  const agentActivity =
    rawActivity && typeof rawActivity === "object"
      ? {
          outcome: String(rawActivity.outcome || "ok"),
          steps: Array.isArray(rawActivity.steps)
            ? rawActivity.steps.filter((s): s is string => typeof s === "string")
            : [],
          changes: Array.isArray(rawActivity.changes)
            ? rawActivity.changes.filter((s): s is string => typeof s === "string")
            : [],
          discrepancies: Array.isArray(rawActivity.discrepancies)
            ? rawActivity.discrepancies.filter((s): s is string => typeof s === "string")
            : [],
        }
      : null;
  return {
    section: data.section,
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
    assistantMessage:
      data.assistantMessage ??
      `Updated ${data.section.title}. Review the draft above.`,
    draftChanged: data.draftChanged !== false,
    suggestedFix,
    agentActivity,
  };
}

export async function downloadProposalDocx(rfpId: string): Promise<void> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/export/docx`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = "Word export failed";
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
    } catch {
      if (text.trim()) detail = text.slice(0, 200);
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="([^"]+)"/i);
  const rawName = decodeURIComponent(match?.[1] || match?.[2] || "proposal.docx");
  const filename = rawName.endsWith(".docx") ? rawName : `${rawName}.docx`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function exportProposalToGoogleDoc(rfpId: string): Promise<{
  documentId: string;
  documentUrl: string;
  title: string;
  sectionCount: number;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/export/google-doc`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const text = await res.text();
  let data: {
    detail?: string;
    documentId?: string;
    document_id?: string;
    documentUrl?: string;
    document_url?: string;
    title?: string;
    sectionCount?: number;
    section_count?: number;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error(
      res.status >= 500
        ? "Export failed on the server. Wait a moment and try again."
        : "Invalid response from Google Doc export."
    );
  }
  if (!res.ok) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Google Doc export failed"
    );
  }
  const documentId = data.documentId ?? data.document_id;
  const documentUrl = data.documentUrl ?? data.document_url;
  if (!documentId || !documentUrl) {
    throw new Error("Google Doc export returned incomplete data");
  }
  return {
    documentId,
    documentUrl,
    title: data.title ?? "Proposal",
    sectionCount: data.sectionCount ?? data.section_count ?? 0,
  };
}

export interface KeyPersonaItem {
  id: string;
  name: string;
  title: string;
  hasResume: boolean;
  sourceFile: string;
  bioSnippet?: string;
  retired?: boolean;
}

export interface KeyPersonasResponse {
  total: number;
  personas: KeyPersonaItem[];
  selectedPersonaIds?: string[];
}

export async function fetchKeyPersonas(
  rfpId?: string,
  signal?: AbortSignal
): Promise<KeyPersonasResponse> {
  const primaryUrl = rfpId
    ? `/api/rfps/${encodeURIComponent(rfpId)}/proposal/key-personas`
    : `/api/knowledge-base/key-personas`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const res = await fetch(primaryUrl, {
      cache: "no-store",
      signal: signal || controller.signal,
    });
    if (res.ok) {
      return (await res.json()) as KeyPersonasResponse;
    }
  } catch {
    // fallback below
  } finally {
    clearTimeout(timer);
  }

  // Fallback query
  try {
    const fallbackRes = await fetch("/api/knowledge-base/key-personas", {
      cache: "no-store",
    });
    if (fallbackRes.ok) {
      return (await fallbackRes.json()) as KeyPersonasResponse;
    }
  } catch {
    // ignore
  }

  return { total: 0, personas: [] };
}

export interface SaveKeyPersonasResult {
  ok: boolean;
  biosSynced?: boolean;
  draft?: ProposalOutline;
}

export async function saveProposalKeyPersonas(
  rfpId: string,
  selectedPersonaIds: string[]
): Promise<SaveKeyPersonasResult> {
  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/proposal/key-personas`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selectedPersonaIds }),
    }
  );
  if (!res.ok) return { ok: false };
  try {
    const data = (await res.json()) as {
      biosSynced?: boolean;
      draft?: ApiProposalDraft;
    };
    return {
      ok: true,
      biosSynced: Boolean(data.biosSynced),
      draft: data.draft ? apiDraftToOutline(data.draft) : undefined,
    };
  }   catch {
    return { ok: true };
  }
}

export async function setKeyPersonaRetired(input: {
  personId: string;
  name: string;
  retired: boolean;
}): Promise<KeyPersonasResponse | null> {
  const res = await fetch("/api/knowledge-base/key-personas/retired", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return null;
  return (await res.json()) as KeyPersonasResponse;
}

