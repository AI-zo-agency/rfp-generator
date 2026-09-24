import { describe, expect, test } from "vitest";
import {
  availableTracksFromAnalysis,
  bidScopeIsReady,
} from "./bid-scope";

describe("bid-scope", () => {
  test("derives tracks from capability matrix when availableTracks missing", () => {
    expect(
      availableTracksFromAnalysis({
        capabilityMatrix: [
          { track: "III.A" },
          { track: "" },
          { track: "III.B" },
        ],
      }),
    ).toEqual(["III.A", "III.B"]);
  });

  test("prefers availableTracks when present", () => {
    expect(
      availableTracksFromAnalysis({
        availableTracks: ["Role B"],
        capabilityMatrix: [{ track: "Role A" }],
      }),
    ).toEqual(["Role B"]);
  });

  test("ready only when locked on multi-track", () => {
    expect(bidScopeIsReady(["A", "B"], null, [])).toBe(false);
    expect(bidScopeIsReady(["A", "B"], "2026-09-23T00:00:00Z", ["B"])).toBe(
      true,
    );
    expect(bidScopeIsReady(["A"], null, [])).toBe(true);
  });
});
