import type { RfpRecord, RfpStage } from "@/types/rfp";

export interface ProcessStep {
  id: RfpStage;
  step: number;
  title: string;
  description: string;
}

export const RFP_PROCESS_STEPS: ProcessStep[] = [
  {
    id: "intake",
    step: 1,
    title: "Knowledge Base",
    description: "Facts, pricing, bios, case studies, won proposals",
  },
  {
    id: "intake",
    step: 2,
    title: "RFP Intake",
    description: "JustWin — detect new RFPs, pull metadata & PDF",
  },
  {
    id: "go_no_go",
    step: 3,
    title: "Go / No-Go",
    description: "Parse RFP, score fit & worth-it, recommend bid or pass",
  },
  {
    id: "compliance",
    step: 4,
    title: "Compliance Map",
    description: "Checklist, sections, page limits, required questions",
  },
  {
    id: "sections_1_3",
    step: 5,
    title: "Sections 1–3",
    description: "Auto-fill overview, bios, case studies from knowledge base",
  },
  {
    id: "sections_4_5",
    step: 6,
    title: "Sections 4–5",
    description: "Draft approach & scope using RFP context & Writing Guide",
  },
  {
    id: "pricing",
    step: 7,
    title: "Pricing",
    description: "Suggest line items, tiers, qualifying language",
  },
  {
    id: "review",
    step: 8,
    title: "Pre-Submit Review",
    description: "Voice, verification, and compliance checks",
  },
  {
    id: "export",
    step: 9,
    title: "Export",
    description: "Design-ready manuscript for layout & submission",
  },
];

export const STAGE_LABELS: Record<RfpStage, string> = {
  intake: "Intake",
  go_no_go: "Go / No-Go",
  compliance: "Compliance",
  sections_1_3: "Sections 1–3",
  sections_4_5: "Sections 4–5",
  pricing: "Pricing",
  review: "Pre-Submit Review",
  export: "Export",
  submitted: "Submitted",
  won: "Won",
  lost: "Lost",
  passed: "Passed",
};

/**
 * Go/No-Go already ran, but scoring was blocked because the upload is not a
 * complete RFP (packet gate). Distinct from never-run intake.
 */
export function isGoNoGoScoringBlocked(rfp: RfpRecord): boolean {
  const analysis = rfp.goNoGoAnalysis;
  if (!analysis) return false;
  if (analysis.packetRead?.blocksScoring) return true;
  if (analysis.provider === "packet-gate") return true;
  return false;
}

/** Analysis exists but could not produce Fit/Worth scores (incomplete / thin packet). */
export function isGoNoGoUnscored(rfp: RfpRecord): boolean {
  const analysis = rfp.goNoGoAnalysis;
  if (!analysis) return false;
  if (isGoNoGoScoringBlocked(rfp)) return true;
  return Boolean(analysis.insufficientData);
}

/** Score cell / header label when there is no numeric Overall Go Score. */
export function formatGoNoGoScoreDisplay(rfp: RfpRecord): string {
  if (isGoNoGoScoringBlocked(rfp)) return "Awaiting packet";
  if (rfp.goNoGoAnalysis?.insufficientData) return "Needs content";
  return "Pending";
}

/** Compact badge when Fit ran but could not score yet. */
export function formatGoNoGoBadgeLabel(rfp: RfpRecord): string {
  if (isGoNoGoScoringBlocked(rfp)) return "Awaiting packet";
  if (rfp.goNoGoAnalysis?.insufficientData) return "Needs content";
  return "—";
}

/** Plain-language workflow step for pipeline tables (not generic "Go / No-Go" for every row). */
export function getWorkflowStepDisplay(rfp: RfpRecord): {
  label: string;
  hint: string;
} {
  if (rfp.goNoGo === "no_go" || rfp.stage === "passed") {
    return { label: "Not bidding", hint: "Marked pass — no proposal draft" };
  }
  if (isGoNoGoScoringBlocked(rfp)) {
    return {
      label: "Awaiting full RFP",
      hint: "Add the complete packet when it arrives, then re-run Fit",
    };
  }
  if (rfp.goNoGoAnalysis?.insufficientData) {
    return {
      label: "Needs RFP content",
      hint: "Add more packet materials, then re-run Fit",
    };
  }
  if (isNewIntake(rfp)) {
    return {
      label: "New intake",
      hint: "Upload or sync RFP, then run Go/No-Go analysis",
    };
  }
  if (needsGoNoGoDecision(rfp)) {
    return {
      label: "Awaiting bid decision",
      hint: "Review Overall Go Score and confirm Go or Pass",
    };
  }
  const hints: Partial<Record<RfpStage, string>> = {
    intake: "RFP received — run analysis when ready",
    go_no_go: "Analysis done — open RFP and confirm Go",
    compliance: "Map requirements and build outline",
    sections_1_3: "Draft Sections 1–3 from knowledge base",
    sections_4_5: "Draft RFP-mapped sections",
    pricing: "Build budget and fee schedule",
    review: "Pre-submit review and gap fixes",
    export: "Export final manuscript",
    submitted: "Submitted to buyer",
    won: "Awarded",
    lost: "Not selected",
  };
  const label = STAGE_LABELS[rfp.stage] ?? rfp.stage;
  return {
    label,
    hint: hints[rfp.stage] ?? "Continue in proposal workspace",
  };
}

const PROPOSAL_STAGES: RfpStage[] = [
  "compliance",
  "sections_1_3",
  "sections_4_5",
  "pricing",
  "review",
  "export",
];

/** Go/No-Go analysis is done but a human has not confirmed Go yet. */
export function needsGoNoGoDecision(rfp: RfpRecord): boolean {
  if (rfp.goNoGo === "go" || rfp.goNoGo === "no_go" || rfp.stage === "passed") {
    return false;
  }
  const analysis = rfp.goNoGoAnalysis;
  return Boolean(analysis && !analysis.insufficientData);
}

/** Marked Go and actively in the proposal drafting pipeline. */
export function isProposalInProgress(rfp: RfpRecord): boolean {
  return rfp.goNoGo === "go" && PROPOSAL_STAGES.includes(rfp.stage);
}

/**
 * True when a proposal draft was started or finished for this RFP.
 * Used to spare overdue RFPs that already have manuscript work.
 */
export function hasGeneratedProposal(rfp: RfpRecord): boolean {
  if (rfp.googleDocUrl) return true;
  if (
    rfp.stage === "submitted" ||
    rfp.stage === "won" ||
    rfp.stage === "lost" ||
    rfp.stage === "export" ||
    rfp.stage === "review"
  ) {
    return true;
  }
  return PROPOSAL_STAGES.includes(rfp.stage);
}

/** Newly synced or manual intake — Go/No-Go has never been run. */
export function isNewIntake(rfp: RfpRecord): boolean {
  if (rfp.stage !== "intake" && rfp.stage !== "go_no_go") {
    return false;
  }
  // An incomplete-packet / insufficient-data result still means analysis ran.
  return !rfp.goNoGoAnalysis;
}
