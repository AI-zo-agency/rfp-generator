import { describe, expect, it } from "vitest";
import { targetedFixSectionChipState } from "./proposal-scan-step-groups";

describe("targetedFixSectionChipState", () => {
  it("lights every in-flight section, not only the first", () => {
    const done = ["sec-a"];
    const active = ["sec-b", "sec-c", "sec-d"];
    expect(targetedFixSectionChipState("sec-a", done, active)).toEqual({
      done: true,
      active: false,
    });
    expect(targetedFixSectionChipState("sec-b", done, active)).toEqual({
      done: false,
      active: true,
    });
    expect(targetedFixSectionChipState("sec-c", done, active)).toEqual({
      done: false,
      active: true,
    });
    expect(targetedFixSectionChipState("sec-e", done, active)).toEqual({
      done: false,
      active: false,
    });
  });
});
