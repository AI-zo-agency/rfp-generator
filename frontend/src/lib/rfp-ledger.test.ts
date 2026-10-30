import { describe, expect, it } from "vitest";
import {
  MAX_DIFF_WORDS,
  bucketLabel,
  formatWordDelta,
  outcomeLabel,
  sourceLabel,
  wordDiff,
} from "./rfp-ledger";

describe("wordDiff", () => {
  it("marks the changed word", () => {
    expect(wordDiff("a b c", "a x c")).toEqual([
      { type: "same", text: "a " },
      { type: "del", text: "b " },
      { type: "add", text: "x " },
      { type: "same", text: "c" },
    ]);
  });

  it("returns one same segment for identical text", () => {
    expect(wordDiff("one two", "one two")).toEqual([{ type: "same", text: "one two" }]);
  });

  it("marks a pure insertion", () => {
    expect(wordDiff("a c", "a b c")).toEqual([
      { type: "same", text: "a " },
      { type: "add", text: "b " },
      { type: "same", text: "c" },
    ]);
  });

  it("treats empty before as all added and empty after as all deleted", () => {
    expect(wordDiff("", "x y")).toEqual([{ type: "add", text: "x y" }]);
    expect(wordDiff("x y", "")).toEqual([{ type: "del", text: "x y" }]);
    expect(wordDiff("", "")).toEqual([]);
  });

  it("falls back to one deletion and one addition for huge inputs", () => {
    const big = (prefix: string) =>
      Array.from({ length: MAX_DIFF_WORDS + 1 }, (_, i) => `${prefix}${i}`).join(" ");
    const result = wordDiff(big("a"), big("b"));
    expect(result.map((s) => s.type)).toEqual(["del", "add"]);
  });
});

describe("labels", () => {
  it("names the four buckets and passes unknown ones through", () => {
    expect(bucketLabel("manual")).toBe("Manual");
    expect(bucketLabel("ai")).toBe("AI-assisted");
    expect(bucketLabel("restore")).toBe("Restore/reset");
    expect(bucketLabel("other")).toBe("Other");
    expect(bucketLabel("weird")).toBe("weird");
  });

  it("names tweak sources", () => {
    expect(sourceLabel("manual_edit")).toBe("Typed edit");
    expect(sourceLabel("chat_apply")).toBe("Chat edit");
    expect(sourceLabel("reset")).toBe("Reset draft");
    expect(sourceLabel("unknown_thing")).toBe("unknown thing");
  });

  it("names run outcomes", () => {
    expect(outcomeLabel("cancelled")).toBe("Stopped");
    expect(outcomeLabel("running")).toBe("Running");
    expect(outcomeLabel("completed")).toBe("Completed");
    expect(outcomeLabel("failed")).toBe("Failed");
  });
});

describe("formatWordDelta", () => {
  it("shows added and removed", () => {
    expect(formatWordDelta(12, 3)).toBe("+12 / −3");
  });

  it("says no change when both are zero", () => {
    expect(formatWordDelta(0, 0)).toBe("no change");
  });
});
