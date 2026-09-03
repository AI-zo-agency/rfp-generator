"use client";

import { useState } from "react";
import {
  ALIGN_RFP_OUTLINE_PHASE,
  PACKET_REDISTRIBUTE_PHASE,
  FULFILL_SCAN_PHASE,
  FULFILL_SCAN_STEP_LABELS,
  FULL_PROPOSAL_STEP_LABELS,
  INTELLIGENCE_STEP_LABELS,
  inProgressPhaseLabel,
  type PipelineInProgressPhase,
  type ProposalPipelineCheckpoint,
} from "@/lib/proposal-pipeline-checkpoint";
import {
  FULFILL_SCAN_STEP_GROUPS,
  TARGETED_FIX_STEP_GROUPS,
  TARGETED_FIX_PREP_STEP_LABELS,
  TARGETED_FIX_FINISH_STEP_LABELS,
  isStaticCompanyBlockSection,
} from "@/lib/proposal-scan-step-groups";
import type { LlmCostRfpBreakdown } from "@/lib/llm-cost-service";
import { capabilityById } from "@/lib/proposal-tool-guide";
import type { ProposalOutline } from "@/types/proposal";

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
  outline?: ProposalOutline | null;
  optimisticScanProfile?: string | null;
  /** From pipeline status. False = Final checks is switched off server-side, so
   *  the rail must not list a phase that can never run. */
  buildFinalizeEnabled?: boolean | null;
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
  outline,
  optimisticScanProfile,
  buildFinalizeEnabled,
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
  // Final checks is OFF BY DEFAULT (config.build_finalize_enabled), so the rail
  // shows it ONLY when the server explicitly reports it enabled. Hiding unless
  // opted in matches the backend default — the inverse would leave a phase in
  // the list that can never run whenever the status field is absent or stale.
  const buildSteps =
    buildFinalizeEnabled === true
      ? FULL_PROPOSAL_STEP_LABELS
      : FULL_PROPOSAL_STEP_LABELS.filter((p) => p.phase !== "build-finalize");

  const generatePhaseIndex = isGenerateRun
    ? buildSteps.findIndex((p) => p.phase === inProgressPhase)
    : -1;
  const isTargetedFix =
    checkpoint?.scanProfile === "targeted_fix" ||
    optimisticScanProfile === "targeted_fix";
  
  // Dynamically map outline sections as targeted fix groups — static
  // Sections 1-3 are excluded since Review & Fix never reviews them.
  const reviewableOutlineSectionLabels = outline?.sections
    ?.filter((s: any) => !isStaticCompanyBlockSection(s.id))
    ?.map((s: any) => s.title || s.id || "Untitled Section");

  // Mirrors the backend's one continuous step sequence in
  // `_run_targeted_fix_per_section_loop`: 2 prep stages, then one step per
  // reviewed section, then 3 finishing stages — presented as three groups so
  // the rail reads as a real sequence instead of one lump of section chips.
  const dynamicTargetedGroups: typeof FULFILL_SCAN_STEP_GROUPS = [
    {
      label: "Structure checks",
      steps: TARGETED_FIX_PREP_STEP_LABELS,
    },
    {
      label: "Review Sections",
      steps: reviewableOutlineSectionLabels || [],
    },
    {
      label: "Final passes",
      steps: TARGETED_FIX_FINISH_STEP_LABELS,
    },
  ];

  // Flat concatenation in the exact backend order: flat index + 1 must equal
  // the backend's step_index, since openWorkflowDetail / the active-chip
  // lookup below resolve via `activeFulfillLabels.indexOf(step) + 1 ===
  // stepIndex`.
  const activeFulfillLabels = isTargetedFix
    ? [
        ...TARGETED_FIX_PREP_STEP_LABELS,
        ...(reviewableOutlineSectionLabels || []),
        ...TARGETED_FIX_FINISH_STEP_LABELS,
      ]
    : FULFILL_SCAN_STEP_LABELS;
    
  const activeFulfillGroups = isTargetedFix ? dynamicTargetedGroups : FULFILL_SCAN_STEP_GROUPS;

  const stepIndex =
    isFulfillRun || isAlignRun || isPlaceRun
      ? checkpoint?.stepIndex ?? null
      : null;
  const stepTotal = isFulfillRun
    ? checkpoint?.stepTotal ?? activeFulfillLabels.length
    : isAlignRun
      ? checkpoint?.stepTotal ?? ALIGN_STEP_LABELS.length
      : isPlaceRun
        ? checkpoint?.stepTotal ?? PLACE_STEP_LABELS.length
        : null;

  const statusLabel = isRunning
    ? (inProgressPhase 
        ? (isTargetedFix && inProgressPhase === FULFILL_SCAN_PHASE ? "Review Sections" : inProgressPhaseLabel(inProgressPhase)) 
        : "Working").toUpperCase()
    : manualFillCount === 0 && hasCompletedFulfillReport
      ? "COMPLETE & CLEAN DRAFT"
      : manualFillCount > 0
        ? `${manualFillCount} ITEM${manualFillCount === 1 ? "" : "S"} NEED INPUT`
        : "READY FOR REVIEW";

  const activityLabel = isRunning ? checkpoint?.activityLabel?.trim() || statusLabel : null;

  // The backend numbers steps over the DRAFT's sections; these chips are built
  // from the outline, which can be ordered differently (the structure pass
  // reorders tabs mid-run). Matching the live activity label to a chip keeps the
  // lit chip the section that is actually running, instead of trusting an index
  // whose two sides can drift. Falls back to the backend index when the label is
  // not in the list (e.g. a tab added mid-run that this outline predates).
  const liveStepLabel = isRunning ? checkpoint?.activityLabel?.trim() || "" : "";
  const labelMatchedStep = liveStepLabel
    ? activeFulfillLabels.findIndex((l) => l === liveStepLabel) + 1
    : 0;
  const effectiveStepIndex =
    isTargetedFix && labelMatchedStep > 0 ? labelMatchedStep : stepIndex;
  const activityDetail = isRunning ? checkpoint?.activityDetail?.trim() || null : null;

  const openWorkflowDetail = () => {
    const activeGroup = activeFulfillGroups.find((group) =>
      group.steps.some(
        (step) => activeFulfillLabels.indexOf(step as any) + 1 === effectiveStepIndex
      )
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
            Step {effectiveStepIndex} of {stepTotal}
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
                : "Hover Build my proposal for what the draft already includes. Review & fix is optional after edits."}
            </p>
          </div>
          {buildPipelineComplete ? (
            <div className="proposal-workflow-section">
              <p className="proposal-workflow-section-label">Build my proposal</p>
              <ul
                className="proposal-workflow-category-steps"
                style={{ "--wf-progress": 1 } as React.CSSProperties}
              >
                {buildSteps.map((p) => (
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
          {activeFulfillGroups.map((group) => {
            const numbers = group.steps.map(
              (step) => activeFulfillLabels.indexOf(step as any) + 1
            );
            const doneCount =
              effectiveStepIndex != null
                ? numbers.filter((n) => n > 0 && effectiveStepIndex > n).length
                : 0;
            const isActive =
              effectiveStepIndex != null && numbers.includes(effectiveStepIndex);
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
                      const n = activeFulfillLabels.indexOf(step as any) + 1;
                      const done =
                        effectiveStepIndex != null && n > 0 && effectiveStepIndex > n;
                      const active = effectiveStepIndex === n;
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
                    ? generatePhaseIndex / buildSteps.length
                    : 0,
              } as React.CSSProperties
            }
          >
            {buildSteps.map((p, i) => {
              const done = generatePhaseIndex >= 0 && i < generatePhaseIndex;
              const active = i === generatePhaseIndex;
              // While Phase 2 ("Intelligence") runs, light up its internal LLM
              // nodes one by one instead of showing a single dot for ~3 minutes.
              const showIntelligenceSubsteps =
                p.phase === "phase-2" && active && inProgressPhase === "phase-2";
              const intelligenceStepIndex = showIntelligenceSubsteps
                ? checkpoint?.stepIndex ?? null
                : null;
              return (
                <li
                  key={p.phase}
                  className={`proposal-workflow-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`}
                >
                  <span className="proposal-workflow-step-dot" aria-hidden />
                  <span className="proposal-workflow-step-label">{p.label}</span>
                  {showIntelligenceSubsteps ? (
                    <ul className="proposal-workflow-substeps">
                      {INTELLIGENCE_STEP_LABELS.map((label, si) => {
                        const n = si + 1;
                        const subDone =
                          intelligenceStepIndex != null && intelligenceStepIndex > n;
                        const subActive = intelligenceStepIndex === n;
                        return (
                          <li
                            key={label}
                            className={`proposal-workflow-step ${subDone ? "is-done" : ""} ${subActive ? "is-active" : ""}`}
                          >
                            <span className="proposal-workflow-step-dot" aria-hidden />
                            <span className="proposal-workflow-step-label">{label}</span>
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
