import { describe, expect, it } from "vitest";
import { correctionSummary, sortCorrections, type KbCorrection } from "./kb-corrections";

function make(overrides: Partial<KbCorrection>): KbCorrection {
  return {
    id: "1",
    customId: "kbnote:1",
    title: "t",
    note: "n",
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "",
    linkedDocumentId: null,
    ...overrides,
  };
}

describe("sortCorrections", () => {
  it("puts the newest correction first", () => {
    const rows = sortCorrections([
      make({ customId: "kbnote:old", createdAt: "2026-08-01T00:00:00Z" }),
      make({ customId: "kbnote:new", createdAt: "2026-08-24T00:00:00Z" }),
    ]);
    expect(rows[0].customId).toBe("kbnote:new");
  });
});

describe("correctionSummary", () => {
  it("uses the note text, trimmed to one line", () => {
    expect(correctionSummary(make({ note: "Ron Comer\nhas retired" }))).toBe(
      "Ron Comer has retired"
    );
  });

  it("falls back to the title when the note is blank", () => {
    expect(correctionSummary(make({ note: "  ", title: "Ron retired" }))).toBe("Ron retired");
  });
});
