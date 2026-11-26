import { describe, expect, it } from "vitest";
import type { RfpRecord } from "@/types/rfp";
import {
  formatGoNoGoScoreDisplay,
  getWorkflowStepDisplay,
  isGoNoGoScoringBlocked,
  isNewIntake,
} from "@/lib/rfp-process";

function baseRfp(over: Partial<RfpRecord> = {}): RfpRecord {
  return {
    id: "rfp-1",
    title: "Test",
    client: "County",
    source: "justwin",
    sector: "Public Sector",
    location: "TX",
    dueDate: "2026-12-14",
    receivedDate: "2026-11-22",
    stage: "intake",
    status: "new",
    priority: "high",
    fitScore: null,
    worthScore: null,
    goNoGo: null,
    assignedTo: null,
    estimatedValue: null,
    lastActivity: "2026-11-22",
    lastActivityNote: "",
    contractRole: "prime",
    ...over,
  };
}

describe("incomplete packet Go/No-Go display", () => {
  const blocked = baseRfp({
    lastActivityNote: "Go/No-Go skipped — upload is not a complete RFP",
    goNoGoAnalysis: {
      fitScore: null,
      worthScore: null,
      recommendation: null,
      insufficientData: true,
      summary: "Incomplete RFP packet — Go/No-Go was not run.",
      scopeMatch: { summary: "", scoreImpact: "", flags: [] },
      sectorMatch: { summary: "", scoreImpact: "", flags: [] },
      compliance: { summary: "", scoreImpact: "", flags: [] },
      teamMatch: { summary: "", scoreImpact: "", flags: [] },
      criticalGaps: [],
      conditions: [],
      provider: "packet-gate",
      packetRead: {
        documentKind: "incomplete_packet",
        blocksScoring: true,
        headline: "Incomplete RFP packet",
        whatItIs: "Solicitation notice only",
      },
    },
  });

  it("does not treat a packet-gate skip as new intake", () => {
    expect(isNewIntake(blocked)).toBe(false);
    expect(isGoNoGoScoringBlocked(blocked)).toBe(true);
  });

  it("labels workflow as awaiting full RFP, not never-run or abandoned", () => {
    const step = getWorkflowStepDisplay(blocked);
    expect(step.label).toBe("Awaiting full RFP");
    expect(step.hint.toLowerCase()).toContain("complete packet");
    expect(step.hint.toLowerCase()).toContain("re-run fit");
    expect(step.hint.toLowerCase()).not.toContain("skipped");
    expect(step.hint.toLowerCase()).not.toContain("then run go/no-go");
  });

  it("keeps Awaiting packet as the badge/score label for blocked Fit", () => {
    expect(formatGoNoGoScoreDisplay(blocked)).toBe("Awaiting packet");
  });

  it("still treats true never-run RFPs as new intake", () => {
    const fresh = baseRfp();
    expect(isNewIntake(fresh)).toBe(true);
    expect(getWorkflowStepDisplay(fresh).label).toBe("New intake");
    expect(formatGoNoGoScoreDisplay(fresh)).toBe("Pending");
  });
});
