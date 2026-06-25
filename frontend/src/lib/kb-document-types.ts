/** RFP knowledge-base document types — metadata only; all ingest to one Supermemory container. */
export const SUPERMEMORY_CONTAINER_TAG = "zo-agency";

export interface KbDocumentType {
  value: string;
  label: string;
  description: string;
}

export const KB_DOCUMENT_TYPES: KbDocumentType[] = [
  {
    value: "verified_facts",
    label: "Verified Facts",
    description: "Company facts, clients, certifications",
  },
  {
    value: "case_study",
    label: "Case Studies",
    description: "Confirmed outcomes and public-approved clients",
  },
  {
    value: "team_bio",
    label: "Team Bios",
    description: "Approved bio text — no paraphrasing",
  },
  {
    value: "pricing",
    label: "Pricing",
    description: "Rate structures, floors, and pricing guides",
  },
  {
    value: "won_proposal",
    label: "Won Proposal",
    description: "Winning proposals — voice and quality benchmark",
  },
  {
    value: "finalist_proposal",
    label: "Finalist Proposal",
    description: "Finalist submissions and competitor context",
  },
  {
    value: "lost_proposal",
    label: "Lost + FOIA",
    description: "Lost proposals and competitor winning submissions",
  },
  {
    value: "scoring_debrief",
    label: "Scoring & Debriefs",
    description: "Evaluation rubrics and award notifications",
  },
  {
    value: "active_rfp",
    label: "Active RFP",
    description: "In-progress bids and working drafts",
  },
  {
    value: "reference",
    label: "Reference / Guides",
    description: "Workflow guides, templates, and research",
  },
];

export function kbDocumentTypeLabel(value: string): string {
  return KB_DOCUMENT_TYPES.find((type) => type.value === value)?.label ?? value;
}

/** Legacy folder-prefix categories from earlier uploads */
const LEGACY_CATEGORY_LABELS: Record<string, string> = {
  "00_": "Reference / Guides",
  "01_": "Verified Facts",
  "02_": "Reference / Guides",
  "03_": "Case Studies",
  "04_": "Team Bios",
  "05_": "Pricing",
  "06_": "Won Proposal",
  "07_": "Finalist Proposal",
  "08_": "Lost + FOIA",
  "09_": "Scoring & Debriefs",
  "10_": "Active RFP",
  "11_": "Reference / Guides",
};

export function resolveCategoryLabel(value: string): string {
  return kbDocumentTypeLabel(value) || LEGACY_CATEGORY_LABELS[value] || value;
}
