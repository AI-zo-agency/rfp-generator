import { describe, expect, it } from "vitest";
import {
  alignGoNoGoRecommendation,
  alignGoNoGoSummary,
} from "./format";

describe("alignGoNoGoRecommendation", () => {
  it("flips no_go to review when overall ≥ 3", () => {
    expect(alignGoNoGoRecommendation("no_go", 3.8)).toBe("review");
  });

  it("keeps no_go when overall is below 3", () => {
    expect(alignGoNoGoRecommendation("no_go", 2.4)).toBe("no_go");
  });

  it("leaves go and review alone", () => {
    expect(alignGoNoGoRecommendation("go", 4)).toBe("go");
    expect(alignGoNoGoRecommendation("review", 3.2)).toBe("review");
  });
});

describe("alignGoNoGoSummary", () => {
  it("rewrites a stale NO-GO lead when showing conditions", () => {
    const out = alignGoNoGoSummary(
      "NO-GO — 5 of 24 required capabilities lack evidence. Overall 3.8/5.",
      "review"
    );
    expect(out.startsWith("GO WITH CONDITIONS")).toBe(true);
    expect(out).not.toMatch(/^NO-GO/i);
  });
});
