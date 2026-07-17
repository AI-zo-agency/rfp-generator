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

interface ApiProposalDraft {
  rfpId: string;
  sections: ApiProposalSection[];
  updatedAt: string;
  generatedAt?: string | null;
  provider?: string | null;
}

export function apiDraftToOutline(draft: ApiProposalDraft): ProposalOutline {
  return {
    updatedAt: draft.updatedAt,
    sections: draft.sections,
  };
}

export function outlineToApiDraft(
  rfpId: string,
  outline: ProposalOutline
): ApiProposalDraft {
  return {
    rfpId,
    updatedAt: outline.updatedAt,
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
  inferResumePhaseFromBlocker,
  phaseIsComplete,
  resolveResumePhase,
  shouldRunPhase,
  type PipelinePhase,
  type ProposalPipelineStatus,
} from "./proposal-pipeline-checkpoint";
import { staticSections1to3Complete } from "./proposal-draft";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "./proposal-stage-timeout";

export type { PipelinePhase, ProposalPipelineStatus };
export {
  buildPipelineStatus,
  PIPELINE_PHASE_LABELS,
  inferResumePhaseFromBlocker,
  pipelineResumeMessage,
  resolveResumePhase,
} from "./proposal-pipeline-checkpoint";

/** Long timeout for staged proposal generation (browser → Next API route). */
const PROPOSAL_STAGE_TIMEOUT_MS_LOCAL = PROPOSAL_STAGE_TIMEOUT_MS;

function proposalPostInit(signal?: AbortSignal): RequestInit {
  const timeout = AbortSignal.timeout(PROPOSAL_STAGE_TIMEOUT_MS_LOCAL);
  const combined =
    signal && typeof AbortSignal.any === "function"
      ? AbortSignal.any([signal, timeout])
      : signal ?? timeout;
  return {
    method: "POST",
    signal: combined,
  };
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    const err = new DOMException("Proposal generation aborted", "AbortError");
    throw err;
  }
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

export function countSectionsWithContent(outline: ProposalOutline): number {
  return outline.sections.filter((s) => s.content?.trim()).length;
}

/** Poll saved draft while a long backend phase runs — surfaces each section as it lands. */
export function startLiveDraftPolling(
  rfpId: string,
  onDraftUpdate: (draft: ProposalOutline) => void
): () => void {
  let lastFingerprint = "";
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
      if (!snapshot.draft) return;
      const next = fingerprint(snapshot.draft);
      if (next !== lastFingerprint) {
        lastFingerprint = next;
        onDraftUpdate(snapshot.draft);
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
  run: () => Promise<T>
): Promise<T> {
  if (!onDraftUpdate) {
    return run();
  }
  const stop = startLiveDraftPolling(rfpId, onDraftUpdate);
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

const STAGE_POLL_INTERVAL_MS = 12_000;
const STAGE_POLL_MAX_MS = 22 * 60 * 1000;
const LIVE_DRAFT_POLL_INTERVAL_MS = 1_500;
/** If checkpoint says in-flight but timestamps never move, backend was killed — don't block resume. */
const IN_FLIGHT_STALE_MS = 90_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    signal?: AbortSignal;
  }
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const signal = options?.signal;
  throwIfAborted(signal);

  const snapshot = await fetchProposalDraft(rfpId);
  let draft = snapshot.draft;
  let research = snapshot.research;

  // Always start from Sections 1–3 when forceRestart is set.
  let resumeFrom: PipelinePhase = options?.forceRestart
    ? "sections-1-3"
    : (options?.startFrom ??
      snapshot.pipelineStatus?.resumeFromPhase ??
      resolveResumePhase(draft, research));

  if (draft && !staticSections1to3Complete(draft)) {
    resumeFrom = "sections-1-3";
  }

  // forceRestart / explicit startFrom must not be redirected to a later phase by
  // validate blockers (e.g. "budget missing" → jump to phase-3-5).
  if (!options?.forceRestart && !options?.startFrom && draft && research) {
    const blocker = validateStagedProposalComplete(draft, research);
    if (!blocker && resumeFrom === "complete") {
      return { draft, research };
    }
    if (blocker) {
      resumeFrom = inferResumePhaseFromBlocker(blocker);
    }
  }

  const run = (phase: PipelinePhase) =>
    options?.forceRestart || shouldRunPhase(phase, resumeFrom);

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
    const snap = await refreshProposalSnapshot(rfpId);
    draft = snap.draft ?? draft;
    research = snap.research ?? research;
    return phaseIsComplete(draft, research, phase);
  }

  if (run("sections-1-3")) {
    if (!(await skipIfPhaseAlreadyFinished("sections-1-3"))) {
      throwIfAborted(signal);
      onProgress?.("sections-1-3");
      draft = await withLiveDraftPolling(rfpId, options?.onDraftUpdate, () =>
        generateProposalSections1to3(rfpId, signal)
      );
      ({ draft, research } = await refreshProposalSnapshot(rfpId));
    }
  }

  if (run("phase-2")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-2"))) {
      throwIfAborted(signal);
      onProgress?.("phase-2");
      research = await runPhase2Retrieval(rfpId, signal);
      ({ draft, research } = await refreshProposalSnapshot(rfpId));
    }
  }

  if (run("phase-3")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3");
      const phase3 = await withLiveDraftPolling(rfpId, options?.onDraftUpdate, () =>
        runPhase3Drafting(rfpId, signal)
      );
      draft = phase3.draft;
      research = phase3.research;
    }
  }

  if (run("phase-3-6-self-edit")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3-6-self-edit"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3-6-self-edit");
      const edited = await withLiveDraftPolling(rfpId, options?.onDraftUpdate, () =>
        runPhase3_6SelfEditWithRecovery(rfpId, signal)
      );
      draft = edited.draft;
      research = edited.research;
    }
  }

  if (run("phase-3-5-budget")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-3-5-budget"))) {
      throwIfAborted(signal);
      onProgress?.("phase-3-5-budget");
      const budgeted = await runPhase3_5BudgetWithRecovery(rfpId, signal);
      research = budgeted.research;
      if (budgeted.draft) draft = budgeted.draft;
    }
  }

  if (run("phase-4-review")) {
    if (!(await skipIfPhaseAlreadyFinished("phase-4-review"))) {
      throwIfAborted(signal);
      onProgress?.("phase-4-review");
      const reviewed = await runPhase4PreSubmitReview(rfpId, signal);
      research = reviewed.research;
    }
  }

  throwIfAborted(signal);

  ({ draft, research } = await refreshProposalSnapshot(rfpId));
  if (!draft || !research) {
    throw new Error("Proposal draft missing after pipeline run.");
  }

  const blocker = validateStagedProposalComplete(draft, research);
  if (blocker) {
    const lower = blocker.toLowerCase();
    if (lower.includes("verify") && research.budget && research.presubmitReview) {
      return { draft, research };
    }
    if (lower.includes("verify") && !options?.forceRestart) {
      throwIfAborted(signal);
      onProgress?.("phase-3-6-self-edit");
      const retry = await runPhase3_6SelfEditWithRecovery(rfpId, signal);
      draft = retry.draft;
      research = retry.research;
      ({ draft, research } = await refreshProposalSnapshot(rfpId));
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
}

async function refreshProposalSnapshot(rfpId: string): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
}> {
  const snapshot = await fetchProposalDraft(rfpId);
  return { draft: snapshot.draft, research: snapshot.research };
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

export async function fetchProposalDraft(rfpId: string): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
  provider?: string | null;
  pipelineStatus: ProposalPipelineStatus | null;
}> {
  const empty = {
    draft: null as ProposalOutline | null,
    research: null as ProposalResearch | null,
    pipelineStatus: null as ProposalPipelineStatus | null,
  };

  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await fetch(`/api/rfps/${rfpId}/proposal`, { cache: "no-store" });
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
    if (!data.draft?.sections?.length) {
      return {
        draft: null,
        research: data.research ?? null,
        pipelineStatus: data.pipelineStatus ?? null,
      };
    }
    const draft = apiDraftToOutline(data.draft);
    const research = data.research ?? null;
    return {
      draft,
      research,
      provider: data.draft.provider,
      pipelineStatus:
        data.pipelineStatus ??
        buildPipelineStatus(draft, research),
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

export async function generateProposalSections1to3(
  rfpId: string,
  signal?: AbortSignal
): Promise<ProposalOutline> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/generate/sections-1-3`,
    proposalPostInit(signal)
  );
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (generation may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Sections 1–3 generation failed");
  }
  if (!data.draft) {
    throw new Error("No draft returned from server");
  }
  return apiDraftToOutline(data.draft);
}

export async function runPhase2Retrieval(
  rfpId: string,
  signal?: AbortSignal
): Promise<ProposalResearch> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-2-retrieval`, {
    ...proposalPostInit(signal),
  });
  const text = await res.text();
  let data: { detail?: string; research?: ProposalResearch };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (Phase 2 may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 2 retrieval failed");
  }
  if (!data.research) {
    throw new Error("No research data returned from server");
  }
  return data.research;
}

export async function runPhase3Drafting(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-3-drafting`, {
    ...proposalPostInit(signal),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (Phase 3 may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 3 drafting failed");
  }
  if (!data.draft || !data.research) {
    throw new Error("No draft or research returned from server");
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research,
  };
}

export async function runPhase3_6SelfEdit(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-3-6-self-edit`, {
    ...proposalPostInit(signal),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    draft?: Parameters<typeof apiDraftToOutline>[0];
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (self-edit may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Self-edit loop failed");
  }
  if (!data.draft || !data.research) {
    throw new Error("No draft returned from self-edit");
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research,
  };
}

export async function runPhase3_5Budget(
  rfpId: string,
  signal?: AbortSignal
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-3-5-budget`, {
    ...proposalPostInit(signal),
  });
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
    throw new Error("Invalid response from server (budget step may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 3.5 budget failed");
  }
  if (!data.budget || !data.research) {
    throw new Error("No budget data returned from server");
  }
  return {
    budget: data.budget,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
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
  const res = await fetch(`/api/rfps/${rfpId}/proposal/pricing/generate`, {
    method: "POST",
  });
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
    throw new Error("Invalid response from server (pricing may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pricing generation failed");
  }
  if (!data.budget || !data.research) {
    throw new Error("No budget data returned from server");
  }
  return {
    budget: data.budget,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
  };
}

export async function runPhase4PreSubmitReview(
  rfpId: string,
  signal?: AbortSignal
): Promise<{ review: PreSubmitReview; research: ProposalResearch }> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-4-review`, {
    ...proposalPostInit(signal),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    review?: PreSubmitReview;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from pre-submit review.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pre-submit review failed");
  }
  if (!data.review || !data.research) {
    throw new Error("No review data returned");
  }
  return { review: data.review, research: data.research };
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
  }
): Promise<{
  section: OutlineSection;
  draft: ProposalOutline;
  research: ProposalResearch | null;
  assistantMessage: string;
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
              selectionText: options.selection.text,
            }
          : {}),
      }),
    }
  );
  const text = await res.text();
  let data: {
    detail?: string;
    section?: OutlineSection;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
    assistantMessage?: string;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (section improve may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Section improve failed");
  }
  if (!data.section || !data.draft) {
    throw new Error("No section data returned from server");
  }
  return {
    section: data.section,
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
    assistantMessage:
      data.assistantMessage ??
      `Updated ${data.section.title}. Review the draft above.`,
  };
}
