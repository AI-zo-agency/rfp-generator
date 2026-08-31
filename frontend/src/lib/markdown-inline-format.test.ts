import { describe, expect, it } from "vitest";
import {
  selectionHasMarker,
  toggleWrapMarkers,
} from "./markdown-inline-format";

describe("toggleWrapMarkers", () => {
  it("wraps a plain selection", () => {
    const { next } = toggleWrapMarkers("hello world", 0, 5, "**");
    expect(next).toBe("**hello** world");
  });

  it("unwraps when markers sit just outside", () => {
    const { next } = toggleWrapMarkers("**hello** world", 2, 7, "**");
    expect(next).toBe("hello world");
  });

  it("unwraps when selection includes the markers", () => {
    const { next } = toggleWrapMarkers("**hello** world", 0, 9, "**");
    expect(next).toBe("hello world");
  });

  it("unwraps when selection is inside an existing bold run", () => {
    const src =
      "**We are zo agency, a full-service partner.**\n\nNext paragraph.";
    const innerStart = src.indexOf("We are");
    const innerEnd = src.indexOf("partner.") + "partner.".length;
    const { next } = toggleWrapMarkers(src, innerStart, innerEnd, "**");
    expect(next.startsWith("We are zo agency")).toBe(true);
    expect(next).not.toContain("**");
  });

  it("strips accidental double wrap to plain text", () => {
    const { next } = toggleWrapMarkers("****hello****", 0, 13, "**");
    expect(next).toBe("hello");
  });
});

describe("selectionHasMarker", () => {
  it("detects bold from an interior selection", () => {
    const src = "**bold phrase** here";
    expect(selectionHasMarker(src, 4, 8, "**")).toBe(true);
    expect(selectionHasMarker(src, 16, 20, "**")).toBe(false);
  });
});
