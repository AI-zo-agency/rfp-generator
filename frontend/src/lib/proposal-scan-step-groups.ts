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
