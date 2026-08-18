import { describe, expect, it } from "vitest";
import { shouldSkipCompletedPhase } from "./proposal-pipeline-checkpoint";

describe("shouldSkipCompletedPhase", () => {
  it("does not skip later phases after this click re-ran Intelligence", () => {
    expect(
      shouldSkipCompletedPhase({
        phase: "phase-3",
        lastRanThisInvocation: "phase-2",
        lastCompletedPhase: "phase-2",
        completedOnServer: true,
        locallyComplete: true,
      })
    ).toBe(false);
  });

  it("does not skip RFP drafting when checkpoint only reached Intelligence", () => {
    expect(
      shouldSkipCompletedPhase({
        phase: "phase-3",
        lastRanThisInvocation: null,
        lastCompletedPhase: "phase-2",
        completedOnServer: true,
        locallyComplete: true,
      })
    ).toBe(false);
  });

  it("skips a phase the checkpoint has reached and the manuscript still has", () => {
    expect(
      shouldSkipCompletedPhase({
        phase: "phase-2",
        lastRanThisInvocation: null,
        lastCompletedPhase: "phase-2",
        completedOnServer: true,
        locallyComplete: true,
      })
    ).toBe(true);
  });

  it("re-runs a reached phase when local content is gone", () => {
    expect(
      shouldSkipCompletedPhase({
        phase: "phase-3",
        lastRanThisInvocation: null,
        lastCompletedPhase: "phase-3",
        completedOnServer: true,
        locallyComplete: false,
      })
    ).toBe(false);
  });
});
