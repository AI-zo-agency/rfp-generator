"use client";

import {
  FULFILL_SCAN_PHASE,
  FULFILL_SCAN_STEP_LABELS,
  FULL_PROPOSAL_STEP_LABELS,
  PIPELINE_PHASE_LABELS,
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

  const stepLabels = isFulfill ? FULFILL_SCAN_STEP_LABELS : null;

  return (
    <div
      className="proposal-pipeline-progress shrink-0 border-b border-zo-border/60 bg-[#fafbfc] px-3 py-2.5"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-zo-orange">
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
      {activity ? (
        <p className="mt-1 text-sm font-semibold text-foreground">{truncate(activity, 120)}</p>
      ) : (
        <p className="mt-1 text-sm text-zo-text-muted">Starting…</p>
      )}
      {detail ? (
        <p className="mt-0.5 text-[11px] leading-relaxed text-zo-text-muted">{truncate(detail, 200)}</p>
      ) : null}

      {!isFulfill ? (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {FULL_PROPOSAL_STEP_LABELS.map(({ phase, label }) => {
            const active = phaseKey === phase;
            const done =
              fullProposalProgress &&
              fullProposalProgress !== "recovering" &&
              FULL_PROPOSAL_STEP_LABELS.findIndex((s) => s.phase === fullProposalProgress) >
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
