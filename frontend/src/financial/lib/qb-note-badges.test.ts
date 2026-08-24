import { describe, expect, it } from "vitest";
import { badgeForRow, badgeForSignal } from "./qb-note-badges";

describe("badgeForSignal", () => {
  it("maps cash and aged AR stress to High impact", () => {
    expect(badgeForSignal("ap-over-cash", "critical")).toBe("High impact");
    expect(badgeForSignal("ar-late", "critical")).toBe("High impact");
    expect(badgeForSignal("ar-late", "warn")).toBe("High impact");
  });

  it("maps concentration and segment gaps to Risk", () => {
    expect(badgeForSignal("vendor-concentration", "info")).toBe("Risk");
    expect(badgeForSignal("segment-gap", "warn")).toBe("Risk");
  });

  it("maps cost hygiene to Action and slow payers to Watch", () => {
    expect(badgeForSignal("cost-untagged", "warn")).toBe("Action");
    expect(badgeForSignal("slow-payers", "warn")).toBe("Watch");
  });
});

describe("badgeForRow", () => {
  it("maps margin to Opportunity, fix to Action, collect by flag", () => {
    expect(badgeForRow("margin")).toBe("Opportunity");
    expect(badgeForRow("fix")).toBe("Action");
    expect(badgeForRow("collect")).toBe("High impact");
    expect(badgeForRow("collect", true)).toBe("Watch");
  });
});
