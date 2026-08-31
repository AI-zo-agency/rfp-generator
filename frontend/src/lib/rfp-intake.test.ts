import { describe, expect, it } from "vitest";
import { describeRfpIntake } from "./rfp-intake";
import type { RfpRecord } from "@/types/rfp";

function baseRfp(overrides: Partial<RfpRecord> = {}): RfpRecord {
  return {
    id: "rfp-jw-test",
    title: "Test RFP",
    client: "Client",
    source: "justwin",
    sector: "Public Sector",
    location: "CA",
    dueDate: "2026-09-01",
    receivedDate: "2026-08-30",
    stage: "intake",
    status: "new",
    priority: "medium",
    fitScore: null,
    worthScore: null,
    goNoGo: null,
    assignedTo: null,
    estimatedValue: null,
    lastActivity: "2026-08-30T12:00:00.000Z",
    lastActivityNote: "Synced from JustWin (warm leads)",
    contractRole: "prime",
    syncedAt: "2026-08-30T12:00:00.000Z",
    justwinTab: "warm",
    ...overrides,
  };
}

describe("describeRfpIntake", () => {
  it("labels JustWin warm sync with relative time", () => {
    const intake = describeRfpIntake(baseRfp());
    expect(intake.method).toBe("JustWin · Warm");
    expect(intake.syncedLabel).toMatch(/^Synced /);
    expect(intake.tooltip).toContain("JustWin · Warm");
    expect(intake.tooltip).toContain("Lead posted on JustWin");
    expect(intake.justwinDateLabel).toBe("JustWin date Aug 30");
  });

  it("labels manual uploads", () => {
    const intake = describeRfpIntake(
      baseRfp({
        id: "manual-abc",
        source: "manual",
        justwinTab: undefined,
        syncedAt: "2026-08-31T10:00:00.000Z",
      })
    );
    expect(intake.method).toBe("Manual upload");
    expect(intake.justwinDateLabel).toBeUndefined();
  });
});
