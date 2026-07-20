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

/** Plain-language workflow step for pipeline tables (not generic "Go / No-Go" for every row). */
export function getWorkflowStepDisplay(rfp: RfpRecord): {
  label: string;
  hint: string;
} {
  if (rfp.goNoGo === "no_go" || rfp.stage === "passed") {
    return { label: "Not bidding", hint: "Marked pass — no proposal draft" };
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
      hint: "Review fit score and confirm Go or Pass",
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

/** Newly synced or manual intake — analysis not run yet. */
export function isNewIntake(rfp: RfpRecord): boolean {
  if (rfp.stage !== "intake" && rfp.stage !== "go_no_go") {
    return false;
  }
  const analysis = rfp.goNoGoAnalysis;
  return !analysis || Boolean(analysis.insufficientData);
}
