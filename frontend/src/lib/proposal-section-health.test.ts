import { describe, expect, it } from "vitest";
import {
  classifySectionHealth,
  isDeadSection,
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
