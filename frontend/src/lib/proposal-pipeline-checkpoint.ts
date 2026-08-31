import type { ProposalOutline, ProposalResearch } from "@/types/proposal";
import { staticSections1to3Complete } from "@/lib/proposal-draft";

export const FULFILL_SCAN_PHASE = "fulfill-scan";
export const ALIGN_RFP_OUTLINE_PHASE = "align-rfp-outline";
export const PACKET_REDISTRIBUTE_PHASE = "packet-redistribute";

export type PipelinePhase =
  | "sections-1-3"
  | "phase-2"
  | "phase-3"
  | "phase-3-5-budget"
  | "phase-3-6-self-edit"
  | "phase-4-review"
  | "build-finalize"
  | "complete";

export type PipelineInProgressPhase =
  | PipelinePhase
  | typeof FULFILL_SCAN_PHASE
  | typeof ALIGN_RFP_OUTLINE_PHASE
  | typeof PACKET_REDISTRIBUTE_PHASE;

export const PIPELINE_PHASE_ORDER: PipelinePhase[] = [
  "sections-1-3",
  "phase-2",
  "phase-3",
  "phase-3-5-budget",
  "phase-3-6-self-edit",
  "phase-4-review",
  "build-finalize",
];

export const PIPELINE_PHASE_LABELS: Record<PipelinePhase, string> = {
  "sections-1-3": "Sections 1–3",
  "phase-2": "Phase 2 intelligence",
  "phase-3": "Phase 3 drafting",
  "phase-3-6-self-edit": "Senior editor polish",
  "phase-3-5-budget": "Budget build",
  "phase-4-review": "Pre-submit review",
  "build-finalize": "Final checks",
  complete: "Complete",
};

export interface ProposalPipelineCheckpoint {
  lastCompletedPhase?: PipelinePhase | null;
  inProgressPhase?: PipelineInProgressPhase | null;
  lastFailedPhase?: PipelineInProgressPhase | null;
  lastError?: string | null;
  resumeFromPhase?: PipelinePhase | null;
  activityLabel?: string | null;
  activityDetail?: string | null;
  stepIndex?: number | null;
  stepTotal?: number | null;
  lastCompletedFulfillStep?: number | null;
  resumeFulfillStep?: number | null;
  /** Draft-content hash + ISO time of the last completed Complete & clean run. */
  lastCleanFulfillScanHash?: string | null;
  lastCleanFulfillScanAt?: string | null;
  updatedAt: string;
}

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

/**
 * UI chip labels for the Scan RFP button (full RFP update).
 *
 * Must stay in sync with FULFILL_STEPS in
 * backend/app/services/proposal_fulfill_rfp_gaps.py — this list is maintained by hand,
 * so a stage added on the backend is invisible here until it is added below.
 */
export const FULFILL_SCAN_STEP_LABELS = [
  "RFP structure (all scored sections)",
  "Closing & submission tabs",
  "Requirement ledger (merge / cut / add)",
  "DQ & gov-policy gate (agentic loop)",
  "Remove duplicate sections",
  "Senior editor review (RFP reviewer)",
  "Budget (regen if missing + thorough)",
  "Consistency repairs",
  "Compliance fabrication guard",
  "Contractor KPIs (Section 2.3)",
  "KB fact-check (Supermemory)",
  "RFP contradiction check (LLM)",
  "Line-by-line KB grounding (async)",
  "Remove optional VERIFY/MANUAL FILL",
  "Compact manuscript (remove duplicates)",
  "Page limit & anti-invention (Ralph)",
  "Pre-submit refresh",
  "Submission readiness (triage + score)",
] as const;

export const FULL_PROPOSAL_STEP_LABELS: { phase: PipelinePhase; label: string }[] = [
  { phase: "sections-1-3", label: "Sections 1–3" },
  { phase: "phase-2", label: "Intelligence" },
  { phase: "phase-3", label: "RFP tabs" },
  { phase: "phase-3-5-budget", label: "Budget" },
  { phase: "phase-3-6-self-edit", label: "Senior editor" },
  { phase: "phase-4-review", label: "Review" },
  { phase: "build-finalize", label: "Final checks" },
];

export interface ProposalPipelineStatus {
  resumeFromPhase: PipelinePhase;
  completedPhases: PipelinePhase[];
  isComplete: boolean;
  canResume: boolean;
  lastCompletedPhase?: PipelinePhase | null;
  lastFailedPhase?: PipelineInProgressPhase | null;
  lastError?: string | null;
  inProgressPhase?: PipelineInProgressPhase | null;
  phaseLabels: Record<string, string>;
  checkpoint?: ProposalPipelineCheckpoint | null;
  /** True when a Complete & clean run finished and the draft has not been edited
   * since. Server-derived, so it survives refresh and is the same for every user. */
  fulfillScanUpToDate?: boolean | null;
  /** ISO time the last Complete & clean run finished (server-side). */
  fulfillScanCompletedAt?: string | null;
}

/** True when the one-click build pipeline finished (through Final checks). */
export function isBuildPipelineComplete(
  pipelineStatus: ProposalPipelineStatus | null | undefined,
  research: ProposalResearch | null | undefined
): boolean {
  if (pipelineStatus?.isComplete) return true;
  if (pipelineStatus?.completedPhases?.includes("build-finalize")) return true;
  const last = research?.pipelineCheckpoint?.lastCompletedPhase;
  return last === "build-finalize" || last === "complete";
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

export function phaseIndex(phase: PipelinePhase): number {
  if (phase === "complete") return PIPELINE_PHASE_ORDER.length;
  return PIPELINE_PHASE_ORDER.indexOf(phase);
}

/**
 * Whether generateFullProposalStaged should skip a phase that looks finished.
 *
 * Stale completedPhases / leftover manuscript from a prior run must not skip
 * later work after this click already re-ran an earlier phase, and must not
 * skip a phase the checkpoint has not reached yet.
 */
export function shouldSkipCompletedPhase(options: {
  phase: PipelinePhase;
  lastRanThisInvocation: PipelinePhase | null;
  lastCompletedPhase?: PipelinePhase | null;
  completedOnServer: boolean | null;
  locallyComplete: boolean;
}): boolean {
  const {
    phase,
    lastRanThisInvocation,
    lastCompletedPhase,
    completedOnServer,
    locallyComplete,
  } = options;
  if (
    lastRanThisInvocation &&
    phaseIndex(phase) > phaseIndex(lastRanThisInvocation)
  ) {
    return false;
  }
  if (
    lastCompletedPhase &&
    lastCompletedPhase !== "complete" &&
    phaseIndex(lastCompletedPhase) >= 0 &&
    phaseIndex(phase) > phaseIndex(lastCompletedPhase)
  ) {
    return false;
  }
  if (completedOnServer === true) return locallyComplete;
  return false;
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
  if (phase === "build-finalize") {
    const last = research.pipelineCheckpoint?.lastCompletedPhase;
    if (!last) return false;
    const order = PIPELINE_PHASE_ORDER;
    const lastIdx = order.indexOf(last as PipelinePhase);
    const finalizeIdx = order.indexOf("build-finalize");
    return lastIdx >= 0 && finalizeIdx >= 0 && lastIdx >= finalizeIdx;
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
  if (
    cp?.lastFailedPhase &&
    (PIPELINE_PHASE_ORDER as readonly string[]).includes(cp.lastFailedPhase)
  ) {
    const failed = cp.lastFailedPhase as PipelinePhase;
    if (failed === "phase-3-6-self-edit") {
      const err = (cp.lastError ?? "").toLowerCase();
      if (
        (err.includes("verify") || err.includes("placeholder")) &&
        !phaseIsComplete(draft, research, "phase-3-5-budget")
      ) {
        return "phase-3-5-budget";
      }
    }
    return failed;
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
    (cp?.lastFailedPhase &&
    (PIPELINE_PHASE_ORDER as readonly string[]).includes(cp.lastFailedPhase)
      ? (cp.lastFailedPhase as PipelinePhase)
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
    // Derived from the checkpoint the Celery task persisted, so the "already
    // ran / run again" state survives refresh and is the same for every user.
    fulfillScanCompletedAt: cp?.lastCleanFulfillScanAt ?? null,
    fulfillScanUpToDate: Boolean(
      cp?.lastCleanFulfillScanHash &&
        cp.lastCleanFulfillScanHash === computeDraftContentHash(draft)
    ),
  };
}

/**
 * Draft-content fingerprint that matches the backend's compute_fulfill_scan_hash
 * (sha256 of "id\x00content" joined by \x01). Lets the UI tell "nothing changed
 * since the last scan" from a bare re-save, without a fragile timestamp.
 */
function computeDraftContentHash(draft: ProposalOutline | null): string {
  if (!draft) return "";
  const blob = draft.sections
    .map((s) => `${s.id} ${s.content ?? ""}`)
    .join("");
  return sha256Hex(blob);
}

/** Minimal synchronous SHA-256 (hex) — no async crypto.subtle, so it works
 *  inside the synchronous buildPipelineStatus on both server and client. */
function sha256Hex(message: string): string {
  const bytes = new TextEncoder().encode(message);
  const K = SHA256_K;
  let h0 = 0x6a09e667,
    h1 = 0xbb67ae85,
    h2 = 0x3c6ef372,
    h3 = 0xa54ff53a,
    h4 = 0x510e527f,
    h5 = 0x9b05688c,
    h6 = 0x1f83d9ab,
    h7 = 0x5be0cd19;
  const l = bytes.length;
  const withOne = l + 1;
  const k = (((56 - (withOne % 64)) % 64) + 64) % 64;
  const total = withOne + k + 8;
  const buf = new Uint8Array(total);
  buf.set(bytes);
  buf[l] = 0x80;
  const bitLen = l * 8;
  const dv = new DataView(buf.buffer);
  dv.setUint32(total - 4, bitLen >>> 0, false);
  dv.setUint32(total - 8, Math.floor(bitLen / 0x100000000), false);
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  const w = new Uint32Array(64);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, hh = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + hh) | 0;
  }
  const toHex = (x: number) => (x >>> 0).toString(16).padStart(8, "0");
  return (
    toHex(h0) + toHex(h1) + toHex(h2) + toHex(h3) +
    toHex(h4) + toHex(h5) + toHex(h6) + toHex(h7)
  );
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export function shouldRunPhase(
  phase: PipelinePhase,
  resumeFrom: PipelinePhase
): boolean {
  if (resumeFrom === "complete") return false;
  return phaseIndex(phase) >= phaseIndex(resumeFrom);
}

export function inProgressPhaseLabel(phase: PipelineInProgressPhase): string {
  if (phase === FULFILL_SCAN_PHASE) return "Complete & clean draft";
  if (phase === ALIGN_RFP_OUTLINE_PHASE) return "Align to RFP outline";
  if (phase === PACKET_REDISTRIBUTE_PHASE) return "Place content in RFP tabs";
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
      const failed: PipelineInProgressPhase = PIPELINE_PHASE_ORDER.includes(
        cp.inProgressPhase as PipelinePhase
      )
        ? (cp.inProgressPhase as PipelinePhase)
        : cp.lastFailedPhase ?? cp.inProgressPhase ?? "phase-3";
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
  if (phase === ALIGN_RFP_OUTLINE_PHASE) {
    return `Still aligning tabs to the RFP outline (${inProgressPhaseLabel(phase)}). Drafted wording is not being rewritten.`;
  }
  if (phase === PACKET_REDISTRIBUTE_PHASE) {
    return `Still placing content into RFP tabs (${inProgressPhaseLabel(phase)}). Moves are verbatim — no prose rewrite.`;
  }
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
    const failed =
      PIPELINE_PHASE_LABELS[status.lastFailedPhase as PipelinePhase] ??
      inProgressPhaseLabel(status.lastFailedPhase);
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
