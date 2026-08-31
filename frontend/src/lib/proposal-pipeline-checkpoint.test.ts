import { describe, expect, it } from "vitest";
import {
  isBuildPipelineComplete,
  shouldSkipCompletedPhase,
} from "./proposal-pipeline-checkpoint";

describe("isBuildPipelineComplete", () => {
  it("is true when build-finalize is in completedPhases", () => {
    expect(
      isBuildPipelineComplete(
        {
          resumeFromPhase: "complete",
          completedPhases: ["build-finalize"],
          isComplete: true,
          canResume: false,
          phaseLabels: {},
        },
        null
      )
    ).toBe(true);
  });

  it("is true when checkpoint lastCompletedPhase is build-finalize", () => {
    expect(
      isBuildPipelineComplete(null, {
        pipelineCheckpoint: { lastCompletedPhase: "build-finalize" },
      } as import("@/types/proposal").ProposalResearch)
    ).toBe(true);
  });

  it("is false before final checks", () => {
    expect(
      isBuildPipelineComplete(
        {
          resumeFromPhase: "phase-4-review",
          completedPhases: ["phase-4-review"],
          isComplete: false,
          canResume: true,
          phaseLabels: {},
        },
        null
      )
    ).toBe(false);
  });
});

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
