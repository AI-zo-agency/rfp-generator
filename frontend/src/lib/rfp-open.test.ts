import { describe, expect, it } from "vitest";
import { OPEN_CLIENT_DEBOUNCE_MS, shouldSendOpen } from "./rfp-open";

describe("shouldSendOpen", () => {
  it("sends the first open for an RFP", () => {
    expect(shouldSendOpen("a", {}, 1000)).toBe(true);
  });

  it("skips a repeat inside the debounce window", () => {
    expect(shouldSendOpen("a", { a: 1000 }, 30_000)).toBe(false);
  });

  it("sends again once the window has passed", () => {
    expect(shouldSendOpen("a", { a: 1000 }, 1000 + OPEN_CLIENT_DEBOUNCE_MS + 1)).toBe(true);
  });

  it("treats another RFP separately", () => {
    expect(shouldSendOpen("b", { a: 1000 }, 2000)).toBe(true);
  });
});
