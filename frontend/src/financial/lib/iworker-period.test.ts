import { describe, expect, it } from "vitest";
import { entryDateInPeriod, parseSheetDate } from "./iworker-period";

describe("iworker-period", () => {
  it("parses sheet dates", () => {
    expect(parseSheetDate("May 13, 2026")?.toISOString().slice(0, 10)).toBe("2026-05-13");
    expect(parseSheetDate("nope")).toBeNull();
  });

  it("includes weekend dates in Mon–Sun range", () => {
    expect(entryDateInPeriod("May 16, 2026", "2026-05-11", "2026-05-17")).toBe(true);
    expect(entryDateInPeriod("May 10, 2026", "2026-05-11", "2026-05-17")).toBe(false);
  });
});
