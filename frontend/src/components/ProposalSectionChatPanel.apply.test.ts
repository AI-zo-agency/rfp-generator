import { describe, expect, it } from "vitest";
import { composeApplyFixInstruction } from "./compose-apply-fix-instruction";

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
