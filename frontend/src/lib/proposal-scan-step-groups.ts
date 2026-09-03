/**
 * The same stages grouped into the four things the pass actually does.
 *
 * Twenty flat chips read as noise — the reader cannot tell a structural step from a
 * fact check, and the one that matters is lost among the rest. Grouping keeps every
 * stage visible (so nothing runs unseen) while making the shape of the run legible.
 */
export const FULFILL_SCAN_STEP_GROUPS: {
  label: string;
  steps: readonly string[];
}[] = [
  {
    label: "Structure",
    steps: [
      "Closing & submission tabs",
      "RFP structure (all scored sections)",
      "Requirement ledger (merge / cut / add)",
      "DQ & gov-policy gate (agentic loop)",
      "Remove duplicate sections",
      "Senior editor review (RFP reviewer)",
    ],
  },
  {
    label: "Content",
    steps: [
      "Budget (regen if missing + thorough)",
      "Consistency repairs",
      "Compliance fabrication guard",
      "Contractor KPIs (Section 2.3)",
    ],
  },
  {
    label: "Fact-check",
    steps: [
      "KB fact-check (Supermemory)",
      "RFP contradiction check (LLM)",
      "Remove optional VERIFY/MANUAL FILL",
      "Line-by-line KB grounding (async)",
    ],
  },
  {
    label: "Review & submit",
    steps: [
      "Compact manuscript (remove duplicates)",
      "Page limit & anti-invention (Ralph)",
      "Pre-submit refresh",
      "Submission readiness (triage + score)",
    ],
  },
];

export const TARGETED_FIX_STEP_GROUPS: {
  label: string;
  steps: readonly string[];
}[] = [
  {
    label: "Structure",
    steps: [
      "RFP structure (all scored sections)",
      "Closing & submission tabs",
    ],
  },
  {
    label: "Content",
    steps: [
      "Compliance fabrication guard",
    ],
  },
  {
    label: "Fact-check",
    steps: [
      "KB fact-check (Supermemory)",
      "RFP contradiction check (LLM)",
    ],
  },
  {
    label: "Review & submit",
    steps: [
      "Pre-submit refresh",
      "Submission readiness (triage + score)",
    ],
  },
];

/**
 * Static Sections 1-3 (company / team / our work). Review & Fix skips these,
 * so they must not appear as chips — mirrors
 * `_is_static_company_block_section` in proposal_fulfill_rfp_gaps.py.
 */
export function isStaticCompanyBlockSection(sectionId: string | undefined): boolean {
  const id = sectionId ?? "";
  return (
    id.startsWith("section-1-") ||
    id.startsWith("section-2-") ||
    id.startsWith("section-3-")
  );
}

export const TARGETED_FIX_STEP_LABELS = [
  "RFP structure (all scored sections)",
  "Closing & submission tabs",
  "Compliance fabrication guard",
  "KB fact-check (Supermemory)",
  "RFP contradiction check (LLM)",
  "Pre-submit refresh",
  "Submission readiness (triage + score)",
] as const;

/**
 * The two prep stages that run BEFORE the per-section loop in
 * `_run_targeted_fix_per_section_loop`, and the three finishing stages that
 * run AFTER it. Together with one step per reviewed section, these form one
 * continuous step sequence — steps 1..2, then 3..(2+N), then (3+N)..(5+N).
 * These strings must match the backend labels in
 * proposal_fulfill_rfp_gaps.py EXACTLY (record_pipeline_activity `label=`
 * arguments in `_run_targeted_fix_per_section_loop`).
 */
export const TARGETED_FIX_PREP_STEP_LABELS = [
  "Checking RFP-mandated sections",
  "Reviewing proposal sections against the RFP",
] as const;

export const TARGETED_FIX_FINISH_STEP_LABELS = [
  "Checking contradictions across sections",
  "Filling gaps from past won proposals",
  "Checking budget against RFP limits",
] as const;
