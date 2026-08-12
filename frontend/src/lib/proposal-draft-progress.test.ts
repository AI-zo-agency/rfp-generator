import { describe, expect, it } from "vitest";
import {
  classifyProposalDraftProgress,
  type ProposalDraftSummary,
} from "./proposal-draft-progress";

describe("classifyProposalDraftProgress", () => {
  it("treats missing summary as not started", () => {
    expect(classifyProposalDraftProgress(null)).toBe("not_started");
    expect(classifyProposalDraftProgress(undefined)).toBe("not_started");
  });

  it("treats zero filled sections as not started", () => {
    const summary: ProposalDraftSummary = {
      rfpId: "rfp-1",
      filledCount: 0,
      sectionCount: 7,
      updatedAt: "",
    };
    expect(classifyProposalDraftProgress(summary)).toBe("not_started");
  });

  it("treats partial fills as in progress", () => {
    expect(
      classifyProposalDraftProgress({
        rfpId: "rfp-1",
        filledCount: 2,
        sectionCount: 7,
        updatedAt: "",
      })
    ).toBe("in_progress");
  });

  it("treats filled >= total as done", () => {
    expect(
      classifyProposalDraftProgress({
        rfpId: "rfp-1",
        filledCount: 7,
        sectionCount: 7,
        updatedAt: "",
      })
    ).toBe("done");
  });
});
