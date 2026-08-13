import { describe, expect, it } from "vitest";
import { FULFILL_SCAN_STEP_GROUPS } from "./proposal-scan-step-groups";

// Duplicated deliberately: importing proposal-pipeline-checkpoint pulls in "@/..."
// modules that do not resolve under vitest in this repo (no vitest config). The
// backend test test_frontend_step_labels_sync.py is what guards the real source list.
const PIPELINE_ORDER = [
  "Closing & submission tabs",
  "RFP structure (all scored sections)",
  "Requirement ledger (merge / cut / add)",
  "DQ & gov-policy gate (agentic loop)",
  "Remove duplicate sections",
  "Senior editor review (RFP reviewer)",
  "Budget (regen if missing + thorough)",
  "Consistency repairs",
  "Compliance fabrication guard",
  "Contractor KPIs (Section 2.3)",
  "KB fact-check (Supermemory)",
  "RFP contradiction check (LLM)",
  "Remove optional VERIFY/MANUAL FILL",
  "Line-by-line KB grounding (async)",
  "Compact manuscript (remove duplicates)",
  "Page limit & anti-invention (Ralph)",
  "Review & quality gate (3 acts)",
  "Pre-submit refresh",
  "Submission readiness (triage + score)",
];

const grouped = FULFILL_SCAN_STEP_GROUPS.flatMap((g) => g.steps);

describe("FULFILL_SCAN_STEP_GROUPS", () => {
  it("covers every stage — grouping must not hide one", () => {
    expect([...grouped].sort()).toEqual([...PIPELINE_ORDER].sort());
  });

  it("lists each stage exactly once", () => {
    expect(grouped.length).toBe(new Set(grouped).size);
  });

  it("preserves pipeline order across groups", () => {
    const positions = grouped.map((s) => PIPELINE_ORDER.indexOf(s));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("keeps groups small enough to scan at a glance", () => {
    for (const group of FULFILL_SCAN_STEP_GROUPS) {
      expect(group.steps.length).toBeGreaterThan(0);
      expect(group.steps.length).toBeLessThanOrEqual(8);
    }
  });

  it("ends with the stages that decide submission", () => {
    const last = FULFILL_SCAN_STEP_GROUPS[FULFILL_SCAN_STEP_GROUPS.length - 1];
    expect(last.steps).toContain("Review & quality gate (3 acts)");
    expect(last.steps).toContain("Submission readiness (triage + score)");
  });
});
