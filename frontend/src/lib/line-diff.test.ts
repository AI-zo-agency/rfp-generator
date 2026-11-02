import { describe, expect, it } from "vitest";
import { countChanges, diffLines } from "./line-diff";

describe("diffLines", () => {
  it("marks added, removed and unchanged lines", () => {
    const lines = diffLines("a\nb\nc", "a\nB\nc\nd");
    expect(lines.map((l) => `${l.kind}:${l.text}`)).toEqual(["same:a", "removed:b", "added:B", "same:c", "added:d"]);
    expect(countChanges(lines)).toEqual({ added: 2, removed: 1 });
  });

  it("reports no changes for identical text", () => {
    expect(countChanges(diffLines("x\ny", "x\ny"))).toEqual({ added: 0, removed: 0 });
  });
});
