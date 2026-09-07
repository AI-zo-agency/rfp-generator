import { describe, expect, it } from "vitest";
import { composeApplyFixInstruction } from "./compose-apply-fix-instruction";
import { assistantBodyAddsUniqueDetail } from "./ProposalSectionChatPanel";

const fix = {
  instruction: "Remove invented Medford phone.",
};

describe("composeApplyFixInstruction", () => {
  it("returns the base instruction when extras are empty", () => {
    expect(composeApplyFixInstruction(fix, "  ")).toBe(fix.instruction);
  });

  it("appends additional user instructions", () => {
    const out = composeApplyFixInstruction(fix, "keep Bend as-is");
    expect(out).toContain(fix.instruction);
    expect(out).toContain("Additional user instructions:");
    expect(out).toContain("keep Bend as-is");
  });
});

describe("assistantBodyAddsUniqueDetail", () => {
  const activity = {
    outcome: "ok",
    steps: ["Read Case Studies", "Applied edits"],
    changes: ['Updated "Case Studies" (272 → 302 words).'],
    discrepancies: [],
  };

  it("hides apply-fix word-count echo under the recap card", () => {
    expect(
      assistantBodyAddsUniqueDetail(
        "Applied the suggested fix to **Case Studies** (272 → 302 words).",
        activity
      )
    ).toBe(false);
  });

  it("keeps a substantive explanation", () => {
    expect(
      assistantBodyAddsUniqueDetail(
        "Rewrote the Hampton and Umatilla framing to match the KB case studies.",
        activity
      )
    ).toBe(true);
  });

  it("shows body when there is no activity card", () => {
    expect(assistantBodyAddsUniqueDetail("Done.", null)).toBe(true);
  });
});
