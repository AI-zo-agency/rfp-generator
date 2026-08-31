"use client";

import { useState } from "react";
import {
  ALIGN_RFP_OUTLINE_PHASE,
  PACKET_REDISTRIBUTE_PHASE,
  FULFILL_SCAN_PHASE,
  FULFILL_SCAN_STEP_LABELS,
  FULL_PROPOSAL_STEP_LABELS,
  inProgressPhaseLabel,
  type PipelineInProgressPhase,
  type ProposalPipelineCheckpoint,
} from "@/lib/proposal-pipeline-checkpoint";
import { FULFILL_SCAN_STEP_GROUPS } from "@/lib/proposal-scan-step-groups";
import type { LlmCostRfpBreakdown } from "@/lib/llm-cost-service";
import { capabilityById } from "@/lib/proposal-tool-guide";

const FULFILL_TOTAL_STEPS = FULFILL_SCAN_STEP_LABELS.length;

const ALIGN_STEP_LABELS = [
  "Save undo checkpoint",
  "Read RFP tab order",
  "Reorder / stub tabs",
  "Save aligned outline",
] as const;

const PLACE_STEP_LABELS = [
  "Save undo checkpoint",
  "Read RFP tab specs",
  "Plan block placement",
  "Move blocks",
] as const;

interface ProposalWorkflowRailProps {
  checkpoint: ProposalPipelineCheckpoint | null | undefined;
  isRunning: boolean;
  /** Live Generate-proposal phase (e.g. "phase-3-5-budget"), independent of the
   *  persisted checkpoint so the rail tracks generation in real time. */
  fullProposalPhase?: string | null;
  isFulfillScanRunning?: boolean;
  isAlignRunning?: boolean;
  isPlaceRunning?: boolean;
  hasCompletedFulfillReport: boolean;
  buildPipelineComplete?: boolean;
  manualFillCount: number;
  rfpCost: LlmCostRfpBreakdown | null;
  costByRunType: { generate: number; completeScan: number; chat: number };
  fmtUsd: (value: number) => string;
  canCompareToSaved: boolean;
  onCompareToSaved: () => void;
  canViewLastResults: boolean;
  onViewLastResults: () => void;
  goRfpCount?: number;
  onOpenGoRfpPicker?: () => void;
}

function CategoryIcon({ label }: { label: string }) {
  const common = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2 } as const;
  if (label === "Structure") {
    return (
      <svg {...common}>
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M4 10h16M10 4v16" />
      </svg>
    );
  }
  if (label === "Content") {
    return (
      <svg {...common}>
        <path d="M6 4h9l5 5v11a1 1 0 01-1 1H6a1 1 0 01-1-1V5a1 1 0 011-1z" />
        <path d="M9 13h6M9 17h6" strokeLinecap="round" />
      </svg>
    );
  }
  if (label === "Fact-check") {
    return (
      <svg {...common}>
        <circle cx="11" cy="11" r="7" />
        <path d="M8.5 11l1.8 1.8L14 9.3M21 21l-3.4-3.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

export function ProposalWorkflowRail({
  checkpoint,
  isRunning,
  fullProposalPhase,
  isFulfillScanRunning,
  isAlignRunning,
  isPlaceRunning,
  hasCompletedFulfillReport,
  buildPipelineComplete = false,
  manualFillCount,
  rfpCost,
  costByRunType,
  fmtUsd,
  canCompareToSaved,
  onCompareToSaved,
  canViewLastResults,
  onViewLastResults,
  goRfpCount,
  onOpenGoRfpPicker,
}: ProposalWorkflowRailProps) {
  // Categories show every step (agent) by default — the whole scan pipeline is
  // visible in the rail at a glance; a category can be collapsed to tidy up.
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(
    () => new Set()
  );
  const toggleCategory = (label: string) =>
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  const [costOpen, setCostOpen] = useState(false);

  // The live generate phase (from polling) is more current than the persisted
  // checkpoint — prefer it so the rail tracks generation as it moves phase to
  // phase; "recovering" is a transient reconnect state, not a real phase.
  const livePhase =
    fullProposalPhase && fullProposalPhase !== "recovering"
      ? (fullProposalPhase as PipelineInProgressPhase)
      : null;
  const inProgressPhase: PipelineInProgressPhase | null =
    livePhase ?? checkpoint?.inProgressPhase ?? null;
  const isFulfillRun =
    isRunning && (isFulfillScanRunning || inProgressPhase === FULFILL_SCAN_PHASE);
  const isAlignRun =
    isRunning &&
    (Boolean(isAlignRunning) || inProgressPhase === ALIGN_RFP_OUTLINE_PHASE);
  const isPlaceRun =
    isRunning &&
    (Boolean(isPlaceRunning) || inProgressPhase === PACKET_REDISTRIBUTE_PHASE);
  // Generate proposal is running (any pipeline phase that is NOT scan/align/place).
  const isGenerateRun =
    isRunning &&
    !isFulfillRun &&
    !isAlignRun &&
    !isPlaceRun &&
    inProgressPhase != null &&
    inProgressPhase !== FULFILL_SCAN_PHASE &&
    inProgressPhase !== ALIGN_RFP_OUTLINE_PHASE &&
    inProgressPhase !== PACKET_REDISTRIBUTE_PHASE;
  const generatePhaseIndex = isGenerateRun
    ? FULL_PROPOSAL_STEP_LABELS.findIndex((p) => p.phase === inProgressPhase)
    : -1;
  const stepIndex =
    isFulfillRun || isAlignRun || isPlaceRun
      ? checkpoint?.stepIndex ?? null
      : null;
  const stepTotal = isFulfillRun
    ? checkpoint?.stepTotal ?? FULFILL_TOTAL_STEPS
    : isAlignRun
      ? checkpoint?.stepTotal ?? ALIGN_STEP_LABELS.length
      : isPlaceRun
        ? checkpoint?.stepTotal ?? PLACE_STEP_LABELS.length
        : null;

  const statusLabel = isRunning
    ? (inProgressPhase ? inProgressPhaseLabel(inProgressPhase) : "Working").toUpperCase()
    : manualFillCount === 0 && hasCompletedFulfillReport
      ? "COMPLETE & CLEAN DRAFT"
      : manualFillCount > 0
        ? `${manualFillCount} ITEM${manualFillCount === 1 ? "" : "S"} NEED INPUT`
        : "READY FOR REVIEW";

  const activityLabel = isRunning ? checkpoint?.activityLabel?.trim() || statusLabel : null;
  const activityDetail = isRunning ? checkpoint?.activityDetail?.trim() || null : null;

  const openWorkflowDetail = () => {
    const activeGroup = FULFILL_SCAN_STEP_GROUPS.find((group) =>
      group.steps.some((step) => FULFILL_SCAN_STEP_LABELS.indexOf(step as (typeof FULFILL_SCAN_STEP_LABELS)[number]) + 1 === stepIndex)
    );
    // Make sure the running category is expanded (it may have been collapsed).
    if (activeGroup) {
      setCollapsedCategories((prev) => {
        if (!prev.has(activeGroup.label)) return prev;
        const next = new Set(prev);
        next.delete(activeGroup.label);
        return next;
      });
    }
  };

  return (
    <aside className="proposal-workflow-rail custom-scrollbar" aria-label="RFP workflow">
      <p className="proposal-workflow-rail-title">RFP Workflow</p>

      <div className="proposal-workflow-status">
        <span
          className={`proposal-workflow-status-dot ${isRunning ? "is-running" : manualFillCount === 0 && hasCompletedFulfillReport ? "is-done" : ""}`}
          aria-hidden
        />
        <span className="proposal-workflow-status-label">{statusLabel}</span>
        {stepIndex != null && stepTotal ? (
          <span className="proposal-workflow-status-step">
            Step {stepIndex} of {stepTotal}
          </span>
        ) : null}
      </div>

      {activityLabel ? (
        <div className="proposal-workflow-activity-card">
          <p className="proposal-workflow-activity-title">{activityLabel}</p>
          {activityDetail ? (
            <p className="proposal-workflow-activity-detail">{activityDetail}</p>
          ) : null}
          <button
            type="button"
            className="proposal-workflow-view-btn"
            onClick={openWorkflowDetail}
          >
            View workflow
          </button>
        </div>
      ) : null}

      {!isRunning ? (
        <>
          <div className="proposal-workflow-idle">
            <p className="proposal-workflow-idle-title">
              {buildPipelineComplete ? "Build finished" : "No workflow running"}
            </p>
            <p className="proposal-workflow-idle-detail">
              {buildPipelineComplete
                ? manualFillCount > 0
                  ? `Final checks completed (~19 min server run). ${manualFillCount} item${manualFillCount === 1 ? "" : "s"} still need input (forms, signatures, attachments) — use the checklist or section chat.`
                  : "Final checks completed. Review the checklist, then download Word."
                : "Hover Build my proposal for what the draft already includes. Fix outline lives under Staff tools on Review; Review & fix is optional after edits."}
            </p>
          </div>
          {buildPipelineComplete ? (
            <div className="proposal-workflow-section">
              <p className="proposal-workflow-section-label">Build my proposal</p>
              <ul
                className="proposal-workflow-category-steps"
                style={{ "--wf-progress": 1 } as React.CSSProperties}
              >
                {FULL_PROPOSAL_STEP_LABELS.map((p) => (
                  <li key={p.phase} className="proposal-workflow-step is-done">
                    <span className="proposal-workflow-step-dot" aria-hidden />
                    <span className="proposal-workflow-step-label">{p.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : isAlignRun ? (
        <div className="proposal-workflow-section">
          <p className="proposal-workflow-section-label">Order tabs</p>
          <p className="proposal-workflow-idle-detail" style={{ marginBottom: "0.75rem" }}>
            <strong>Does:</strong> {capabilityById("reorder").does}
            <br />
            <strong>Doesn’t:</strong> {capabilityById("reorder").doesnt}
          </p>
          <ul
            className="proposal-workflow-category-steps"
            style={
              {
                "--wf-progress":
                  stepIndex != null && stepTotal
                    ? Math.max(0, (stepIndex - 1) / stepTotal)
                    : 0,
              } as React.CSSProperties
            }
          >
            {ALIGN_STEP_LABELS.map((label, i) => {
              const n = i + 1;
              const done = stepIndex != null && stepIndex > n;
              const active = stepIndex === n;
              return (
                <li
                  key={label}
                  className={`proposal-workflow-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`}
                >
                  <span className="proposal-workflow-step-dot" aria-hidden />
                  <span className="proposal-workflow-step-label">{label}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : isPlaceRun ? (
        <div className="proposal-workflow-section">
          <p className="proposal-workflow-section-label">Place content</p>
          <p className="proposal-workflow-idle-detail" style={{ marginBottom: "0.75rem" }}>
            <strong>Does:</strong> {capabilityById("place").does}
            <br />
            <strong>Doesn’t:</strong> {capabilityById("place").doesnt}
            {activityDetail ? (
              <>
                <br />
                <strong>Now:</strong> {activityDetail}
              </>
            ) : null}
          </p>
          <ul
            className="proposal-workflow-category-steps"
            style={
              {
                "--wf-progress":
                  stepIndex != null && stepTotal
                    ? Math.max(0, (stepIndex - 1) / stepTotal)
                    : 0,
              } as React.CSSProperties
            }
          >
            {PLACE_STEP_LABELS.map((label, i) => {
              const n = i + 1;
              const done = stepIndex != null && stepIndex > n;
              const active = stepIndex === n;
              return (
                <li
                  key={label}
                  className={`proposal-workflow-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`}
                >
                  <span className="proposal-workflow-step-dot" aria-hidden />
                  <span className="proposal-workflow-step-label">{label}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : isFulfillRun ? (
      <div className="proposal-workflow-section">
        <p className="proposal-workflow-section-label">Workflow categories</p>
        <ul className="proposal-workflow-categories">
          {FULFILL_SCAN_STEP_GROUPS.map((group) => {
            const numbers = group.steps.map(
              (step) => FULFILL_SCAN_STEP_LABELS.indexOf(step as (typeof FULFILL_SCAN_STEP_LABELS)[number]) + 1
            );
            const doneCount = stepIndex != null ? numbers.filter((n) => n > 0 && stepIndex > n).length : 0;
            const isActive = stepIndex != null && numbers.includes(stepIndex);
            const expanded = !collapsedCategories.has(group.label);
            return (
              <li key={group.label} className="proposal-workflow-category">
                <button
                  type="button"
                  className={`proposal-workflow-category-row ${isActive ? "is-active" : ""}`}
                  aria-expanded={expanded}
                  onClick={() => toggleCategory(group.label)}
                >
                  <span className="proposal-workflow-category-icon" aria-hidden>
                    <CategoryIcon label={group.label} />
                  </span>
                  <span className="proposal-workflow-category-label">{group.label}</span>
                  <span className="proposal-workflow-category-count">
                    {isRunning ? `${doneCount}/${group.steps.length}` : group.steps.length}
                  </span>
                  <svg
                    className={`proposal-workflow-category-chevron ${expanded ? "is-open" : ""}`}
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    aria-hidden
                  >
                    <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {expanded ? (
                  <ul
                    className="proposal-workflow-category-steps"
                    style={
                      {
                        // Fraction of this category's steps the scan has passed,
                        // drives the emerald "reached this far" connector fill.
                        "--wf-progress": group.steps.length
                          ? doneCount / group.steps.length
                          : 0,
                      } as React.CSSProperties
                    }
                  >
                    {group.steps.map((step) => {
                      const n =
                        FULFILL_SCAN_STEP_LABELS.indexOf(step as (typeof FULFILL_SCAN_STEP_LABELS)[number]) + 1;
                      const done = stepIndex != null && n > 0 && stepIndex > n;
                      const active = stepIndex === n;
                      return (
                        <li
                          key={step}
                          className={`proposal-workflow-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`}
                        >
                          <span className="proposal-workflow-step-dot" aria-hidden />
                          <span className="proposal-workflow-step-label">{step}</span>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
      ) : (
        <div className="proposal-workflow-section">
          <p className="proposal-workflow-section-label">Build my proposal</p>
          <ul
            className="proposal-workflow-category-steps"
            style={
              {
                "--wf-progress":
                  generatePhaseIndex >= 0
                    ? generatePhaseIndex / FULL_PROPOSAL_STEP_LABELS.length
                    : 0,
              } as React.CSSProperties
            }
          >
            {FULL_PROPOSAL_STEP_LABELS.map((p, i) => {
              const done = generatePhaseIndex >= 0 && i < generatePhaseIndex;
              const active = i === generatePhaseIndex;
              return (
                <li
                  key={p.phase}
                  className={`proposal-workflow-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`}
                >
                  <span className="proposal-workflow-step-dot" aria-hidden />
                  <span className="proposal-workflow-step-label">{p.label}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="proposal-workflow-section">
        <p className="proposal-workflow-section-label">Cost summary</p>
        <div className="proposal-workflow-cost">
          <div className="proposal-workflow-cost-row">
            <span className="proposal-workflow-cost-value">
              {fmtUsd(rfpCost?.totalCostUsd ?? 0)}
            </span>
            <span className="proposal-workflow-cost-label">LLM cost</span>
          </div>
          <div className="proposal-workflow-cost-row">
            <span className="proposal-workflow-cost-value">
              {(rfpCost?.callCount ?? 0).toLocaleString()}
            </span>
            <span className="proposal-workflow-cost-label">Calls</span>
          </div>
          <div className="proposal-workflow-cost-row">
            <span className="proposal-workflow-cost-value">{rfpCost?.runCount ?? 0}</span>
            <span className="proposal-workflow-cost-label">Runs</span>
          </div>
        </div>
        <button
          type="button"
          className="proposal-workflow-view-btn"
          onClick={() => setCostOpen((v) => !v)}
        >
          {costOpen ? "Hide breakdown" : "Breakdown"}
        </button>
        {costOpen ? (
          <div className="proposal-workflow-cost-breakdown">
            <div className="proposal-workflow-cost-breakdown-row">
              <span>Generate</span>
              <span>{fmtUsd(costByRunType.generate)}</span>
            </div>
            <div className="proposal-workflow-cost-breakdown-row">
              <span>Complete scan</span>
              <span>{fmtUsd(costByRunType.completeScan)}</span>
            </div>
            <div className="proposal-workflow-cost-breakdown-row">
              <span>Chat edits</span>
              <span>{fmtUsd(costByRunType.chat)}</span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="proposal-workflow-section">
        <p className="proposal-workflow-section-label">Quick actions</p>
        <div className="proposal-workflow-quick-actions">
          <button
            type="button"
            className="proposal-workflow-quick-action"
            disabled={!canCompareToSaved}
            onClick={onCompareToSaved}
          >
            Compare to saved version
          </button>
          <button
            type="button"
            className="proposal-workflow-quick-action"
            disabled={!canViewLastResults}
            onClick={onViewLastResults}
          >
            View last results
          </button>
        </div>
      </div>

      {onOpenGoRfpPicker && goRfpCount ? (
        <button
          type="button"
          onClick={onOpenGoRfpPicker}
          className="proposal-go-picker-btn proposal-workflow-switch-btn"
          title="Switch to another Go RFP"
        >
          Switch RFP
          <span className="proposal-go-picker-count">{goRfpCount}</span>
        </button>
      ) : null}
    </aside>
  );
}
