import { describe, expect, it } from "vitest";
import {
  classifySectionHealth,
  isDeadSection,
  isManuscriptSectionDrafted,
  isSectionDrafted,
} from "./proposal-section-health";
// Same fixture the Python suite asserts, so the two implementations cannot drift.
import fixture from "../../../backend/tests/fixtures/section_health_cases.json";

const cases = fixture.cases as { name: string; content: string; expected: string | null }[];

describe("classifySectionHealth", () => {
  for (const c of cases) {
    it(c.name, () => {
      expect(classifySectionHealth(c.content)).toBe(c.expected);
    });
  }

  it("detects the production comma variant that broke chat recovery", () => {
    // San Benito sections 9, 15, 16. Differs from the canonical em-dash constant
    // by one character, which exact-equality matching skipped.
    expect(isDeadSection("[VERIFY: Section drafting failed, needs manual regeneration]")).toBe(true);
  });

  it("never marks a drafted section with an inline VERIFY chip as dead", () => {
    const drafted =
      "We accept the terms of the exemplar agreement. [VERIFY: authorized signatory] " +
      "The signed page is returned with our submission.";
    expect(isSectionDrafted(drafted)).toBe(true);
  });

  it("treats drafted and dead as exact complements", () => {
    for (const c of cases) {
      expect(isSectionDrafted(c.content)).toBe(!isDeadSection(c.content));
    }
  });
});

describe("isManuscriptSectionDrafted", () => {
  it("does not count a heading-only RFP tab as drafted", () => {
    expect(
      isManuscriptSectionDrafted({
        id: "rfp-sec-licenses",
        title: "Licenses and Certification",
        content: "6. LICENSES AND CERTIFICATION",
      }),
    ).toBe(false);
  });

  it("does not count an RFP draft-stub as drafted", () => {
    expect(
      isManuscriptSectionDrafted({
        id: "rfp-structure-performance-and-outcome-indicators",
        title: "Performance and Outcome Indicators",
        content:
          "## Performance and Outcome Indicators\n\n" +
          "[MANUAL FILL: Draft this RFP-required section — Performance and Outcome Indicators]\n\n" +
          "RFP instructions: Required in this RFP's submission sequence.",
      }),
    ).toBe(false);
  });

  it("still counts real RFP prose as drafted", () => {
    expect(
      isManuscriptSectionDrafted({
        id: "rfp-sec-work-plan",
        title: "Work Plan",
        content:
          "We will run a 12-week anti-stigma campaign with weekly creative reviews, " +
          "paid media flights, and a named project manager. Kickoff follows award within ten business days.",
      }),
    ).toBe(true);
  });
});
