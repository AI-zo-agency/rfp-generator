import { describe, expect, it } from "vitest";
import {
  formatQualityGateLines,
  formatReadinessLines,
  formatScanSummaryLines,
} from "./proposal-snapshot-diff";

describe("formatQualityGateLines", () => {
  it("says so explicitly when the gate did not run", () => {
    // Rendering nothing would be indistinguishable from a clean draft.
    const lines = formatQualityGateLines({ ran: false, stoppedReason: "disabled by config" });
    expect(lines.join(" ")).toContain("did not run");
    expect(lines.join(" ")).toContain("disabled by config");
  });

  it("breaks fixes down by detector so improvements are legible", () => {
    const lines = formatQualityGateLines({
      roundsRun: 2,
      tickets: [
        { outcome: "fixed", detector: "slop" },
        { outcome: "fixed", detector: "slop" },
        { outcome: "fixed", detector: "repetition" },
        { outcome: "manual_fill", detector: "consistency" },
      ],
    });
    const text = lines.join("\n");
    expect(text).toContain("3 fixed");
    expect(text).toContain("1 sent to MANUAL FILL");
    expect(text).toContain("filler removed: 2");
    expect(text).toContain("repetition cut: 1");
  });

  it("surfaces unverified claims as an action, not a statistic", () => {
    const lines = formatQualityGateLines({
      claims: [{ status: "unresolved" }, { status: "verified" }],
    });
    expect(lines.join(" ")).toContain("confirm before submitting: 1");
  });

  it("reports corrected facts separately from unverified ones", () => {
    const lines = formatQualityGateLines({ claims: [{ status: "contradicted" }] });
    expect(lines.join(" ")).toContain("Facts corrected");
  });

  it("reports a clean pass rather than going silent", () => {
    expect(formatQualityGateLines({ tickets: [] }).join(" ")).toContain("no issues found");
  });

  it("returns nothing when there is no gate data at all", () => {
    expect(formatQualityGateLines(undefined)).toEqual([]);
  });
});

describe("formatReadinessLines", () => {
  it("distinguishes 'not measured' from a zero score", () => {
    const lines = formatReadinessLines({ measured: false, score: 0 });
    expect(lines.join(" ")).toContain("not measured");
    expect(lines.join(" ")).not.toContain("0%");
  });

  it("shows the score, verdict and confidence", () => {
    const lines = formatReadinessLines({
      measured: true,
      score: 72,
      verdict: "Not ready: 2 scored gaps outstanding.",
      confidence: "low",
      confidenceNote: "weights unpublished for 6 of 9 sections",
    });
    const text = lines.join("\n");
    expect(text).toContain("72%");
    expect(text).toContain("Not ready");
    expect(text).toContain("weights unpublished for 6 of 9");
  });

  it("calls out open blockers", () => {
    expect(formatReadinessLines({ openDisqualifying: 2 }).join(" ")).toContain(
      "Blockers open: 2"
    );
  });
});

describe("formatScanSummaryLines", () => {
  it("includes the new stages alongside the existing ones", () => {
    const text = formatScanSummaryLines({
      inPlaceFixCount: 3,
      repetitionSweep: 2,
      qualityGate: { tickets: [{ outcome: "fixed", detector: "slop" }] },
      readiness: { measured: true, score: 88, verdict: "Not ready" },
    }).join("\n");
    expect(text).toContain("In-place fixes: 3");
    expect(text).toContain("Repetition sweep: 2");
    expect(text).toContain("Review agent");
    expect(text).toContain("Readiness: 88%");
  });

  it("is unchanged for an old summary with none of the new keys", () => {
    expect(formatScanSummaryLines({ inPlaceFixCount: 1 })).toEqual(["In-place fixes: 1"]);
  });
});
