import { describe, expect, it } from "vitest";
import {
  composeApplyFixInstruction,
  resolveApplyFixTarget,
} from "./compose-apply-fix-instruction";

describe("composeApplyFixInstruction", () => {
  it("returns instruction when extras empty", () => {
    expect(
      composeApplyFixInstruction({ instruction: "Insert VERIFY flag." }, "  ")
    ).toBe("Insert VERIFY flag.");
  });

  it("appends extras", () => {
    const out = composeApplyFixInstruction(
      { instruction: "Insert VERIFY flag." },
      "keep Bend"
    );
    expect(out).toContain("Insert VERIFY flag.");
    expect(out).toContain("keep Bend");
  });
});

describe("resolveApplyFixTarget", () => {
  const sections = [
    { id: "exhibit-2", title: "EXHIBIT 2 — Offeror's Acceptance of RFP Amendments" },
    {
      id: "exhibit-5",
      title: "Exhibit 5 — Campaign Contribution Disclosure",
    },
  ];

  it("uses fix.sectionId even when a different tab is open", () => {
    const target = resolveApplyFixTarget(
      sections,
      {
        sectionId: "exhibit-5",
        sectionTitle: "Exhibit 5 — Campaign Contribution Disclosure",
      },
      "exhibit-2"
    );
    expect(target?.id).toBe("exhibit-5");
  });

  it("falls back to title when id missing from draft", () => {
    const target = resolveApplyFixTarget(
      sections,
      {
        sectionId: "stale-id",
        sectionTitle: "Exhibit 5 — Campaign Contribution Disclosure",
      },
      "exhibit-2"
    );
    expect(target?.id).toBe("exhibit-5");
  });

  it("uses viewing only when fix id and title cannot resolve", () => {
    const target = resolveApplyFixTarget(
      sections,
      { sectionId: "gone", sectionTitle: "" },
      "exhibit-2"
    );
    expect(target?.id).toBe("exhibit-2");
  });
});
