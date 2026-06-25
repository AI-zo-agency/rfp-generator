import type { RfpStage } from "@/types/rfp";

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
