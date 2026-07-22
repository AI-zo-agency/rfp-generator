"use client";

import {
  FULFILL_SCAN_PHASE,
  FULFILL_SCAN_STEP_LABELS,
  FULL_PROPOSAL_STEP_LABELS,
  PIPELINE_PHASE_LABELS,
  SENIOR_EDITOR_SUBSTEPS,
  seniorEditorSubstepIndex,
  type PipelinePhase,
  type ProposalPipelineCheckpoint,
} from "@/lib/proposal-pipeline-checkpoint";
import type { FullProposalProgress } from "@/lib/proposal-api";

type Props = {
  checkpoint: ProposalPipelineCheckpoint | null | undefined;
  fullProposalProgress: FullProposalProgress | null;
  isFulfillScanRunning?: boolean;
  rfpTabProgress?: { filled: number; total: number } | null;
};

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function ProposalPipelineProgressStrip({
  checkpoint,
  fullProposalProgress,
  isFulfillScanRunning,
  rfpTabProgress,
}: Props) {
  const cp = checkpoint;
  const inProgress = cp?.inProgressPhase;
  const isFulfill =
    isFulfillScanRunning || inProgress === FULFILL_SCAN_PHASE;
  const isRunning = Boolean(fullProposalProgress || isFulfill || inProgress);

  if (!isRunning) return null;

  const phaseKey =
    (fullProposalProgress && fullProposalProgress !== "recovering"
      ? fullProposalProgress
      : inProgress) ?? null;

  const isSeniorEditor =
    phaseKey === "phase-3-6-self-edit" ||
    inProgress === "phase-3-6-self-edit";

  const phaseTitle = isFulfill
    ? "Scan RFP"
    : phaseKey && phaseKey in PIPELINE_PHASE_LABELS
      ? PIPELINE_PHASE_LABELS[phaseKey as PipelinePhase]
      : phaseKey
        ? String(phaseKey)
        : "Working";

  const activity = cp?.activityLabel?.trim();
  const detail = cp?.activityDetail?.trim();
  const stepIndex = cp?.stepIndex ?? null;
  const stepTotal = cp?.stepTotal ?? null;
  const seniorStep = isSeniorEditor
    ? seniorEditorSubstepIndex(activity, stepIndex)
    : null;

  const stepLabels = isFulfill ? FULFILL_SCAN_STEP_LABELS : null;

  return (
    <div
      className={`proposal-pipeline-progress shrink-0 border-b border-zo-border/60 px-3 py-3 ${
        isSeniorEditor ? "proposal-pipeline-progress--editor" : "bg-[#fafbfc]"
      }`}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-zo-orange">
          <span
            className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-zo-orange"
            aria-hidden
          />
          {phaseTitle}
        </span>
        {stepIndex != null && stepTotal != null && stepTotal > 0 ? (
          <span className="text-[11px] tabular-nums text-zo-text-muted">
            Step {stepIndex}/{stepTotal}
          </span>
        ) : null}
        {rfpTabProgress && fullProposalProgress === "phase-3" ? (
          <span className="text-[11px] tabular-nums text-zo-text-muted">
            RFP tabs {rfpTabProgress.filled}/{rfpTabProgress.total}
          </span>
        ) : null}
      </div>

      <div className="mt-2 flex items-start gap-3">
        <span
          className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-zo-orange/25 border-t-zo-orange"
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          {activity ? (
            <p className="text-sm font-semibold text-foreground">
              {truncate(activity, 140)}
            </p>
          ) : (
            <p className="text-sm text-zo-text-muted">
              {isSeniorEditor
                ? "Starting senior editor…"
                : "Starting…"}
            </p>
          )}
          {detail ? (
            <p className="mt-0.5 text-[12px] leading-relaxed text-zo-text-secondary">
              {truncate(detail, 240)}
            </p>
          ) : isSeniorEditor ? (
            <p className="mt-0.5 text-[12px] leading-relaxed text-zo-text-muted">
              Checking facts, filling gaps, fixing weak sections, then a final
              polish.
            </p>
          ) : null}
        </div>
      </div>

      {isSeniorEditor ? (
        <ol className="mt-3 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-5">
          {SENIOR_EDITOR_SUBSTEPS.map((step, i) => {
            const active = seniorStep === i;
            const done = seniorStep != null && seniorStep > i;
            return (
              <li
                key={step.id}
                className={`rounded-lg border px-2.5 py-2 ${
                  active
                    ? "border-zo-orange/40 bg-zo-orange/10"
                    : done
                      ? "border-emerald-200/80 bg-emerald-50/80"
                      : "border-zo-border/70 bg-white/70"
                }`}
              >
                <p
                  className={`text-[11px] font-bold ${
                    active
                      ? "text-zo-orange"
                      : done
                        ? "text-emerald-800"
                        : "text-zo-text-muted"
                  }`}
                >
                  {done ? "✓ " : active ? "● " : ""}
                  {step.label}
                </p>
                <p className="mt-0.5 text-[10px] leading-snug text-zo-text-muted">
                  {step.hint}
                </p>
              </li>
            );
          })}
        </ol>
      ) : !isFulfill ? (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {FULL_PROPOSAL_STEP_LABELS.map(({ phase, label }) => {
            const active = phaseKey === phase;
            const done =
              fullProposalProgress &&
              fullProposalProgress !== "recovering" &&
              FULL_PROPOSAL_STEP_LABELS.findIndex(
                (s) => s.phase === fullProposalProgress
              ) >
                FULL_PROPOSAL_STEP_LABELS.findIndex((s) => s.phase === phase);
            return (
              <li
                key={phase}
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  active
                    ? "bg-zo-orange/15 text-zo-orange"
                    : done
                      ? "bg-emerald-50 text-emerald-800"
                      : "bg-zo-surface text-zo-text-muted"
                }`}
              >
                {label}
              </li>
            );
          })}
        </ol>
      ) : stepLabels ? (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {stepLabels.map((label, i) => {
            const n = i + 1;
            const active = stepIndex === n;
            const done = stepIndex != null && stepIndex > n;
            return (
              <li
                key={label}
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  active
                    ? "bg-zo-orange/15 text-zo-orange"
                    : done
                      ? "bg-emerald-50 text-emerald-800"
                      : "bg-zo-surface text-zo-text-muted"
                }`}
              >
                {label}
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
