import { describe, expect, it } from "vitest";
import {
  buildAlignDiffRows,
  isInternalAlignNote,
} from "./AlignOutlinePreviewModal";

describe("buildAlignDiffRows", () => {
  it("flags moves with from/to positions", () => {
    const rows = buildAlignDiffRows(
      ["Who We Are", "Scope", "Price"],
      ["Who We Are", "Price", "Scope"]
    );
    const moves = rows.filter((r) => r.kind === "moved");
    expect(moves).toHaveLength(2);
    expect(moves.find((m) => m.title === "Scope")?.fromIndex).toBe(1);
    expect(moves.find((m) => m.title === "Scope")?.toIndex).toBe(2);
  });

  it("flags added and removed titles", () => {
    const rows = buildAlignDiffRows(["A", "B"], ["A", "C"]);
    expect(rows.some((r) => r.kind === "added" && r.title === "C")).toBe(true);
    expect(rows.some((r) => r.kind === "removed" && r.title === "B")).toBe(true);
  });

  it("returns empty when lists match", () => {
    expect(buildAlignDiffRows(["A", "B"], ["A", "B"])).toEqual([]);
  });
});

describe("isInternalAlignNote", () => {
  it("hides LLM / Scan developer notes", () => {
    expect(
      isInternalAlignNote(
        "“4.4” missing RFP outline: 4.4.1 — re-run Scan with LLM to reframe."
      )
    ).toBe(true);
    expect(isInternalAlignNote("Confirm insurance limits with Sonja")).toBe(
      false
    );
  });
});
