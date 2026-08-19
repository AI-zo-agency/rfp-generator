import { describe, expect, it } from "vitest";
import type { OutlineSection } from "@/types/proposal";
import {
  buildRfpTabDisplayNumbers,
  sectionListLabel,
  sortManuscriptSections,
  stripLeadingOutlineNumber,
} from "./proposal-outline-tree";

function sec(
  id: string,
  title: string,
  source: OutlineSection["source"] = "rfp",
): OutlineSection {
  return {
    id,
    title,
    wordTarget: 400,
    required: true,
    custom: false,
    content: "",
    status: "outline",
    source,
  };
}

describe("RFP tab list numbering", () => {
  it("strips buyer TOC numbers from titles", () => {
    expect(stripLeadingOutlineNumber("10. Capacity")).toBe("Capacity");
    expect(
      stripLeadingOutlineNumber("1. Transmission Letter — Intent"),
    ).toBe("Transmission Letter — Intent");
    expect(stripLeadingOutlineNumber("3.1 — City of Umatilla")).toBe(
      "City of Umatilla",
    );
  });

  it("keeps Intelligence order instead of sorting by buyer numbers or ids", () => {
    const ordered = sortManuscriptSections([
      sec("section-3-work-umatilla", "3.1 — City of Umatilla", "template"),
      sec("rfp-closing-capacity", "10. Capacity"),
      sec("rfp-sec-transmission", "1. Transmission Letter"),
      sec("rfp-sec-budget", "3. Program Budget"),
    ]);
    expect(ordered.map((s) => s.id)).toEqual([
      "section-3-work-umatilla",
      "rfp-closing-capacity",
      "rfp-sec-transmission",
      "rfp-sec-budget",
    ]);
  });

  it("labels RFP tabs 4, 5, 6… after static 1–3, not 10+ from subsection count", () => {
    const sections = [
      sec("section-1-who-we-are", "1.1 — Who We Are", "template"),
      sec("section-1-org-structure", "1.2 — Organizational Structure", "template"),
      sec("section-1-business-info", "1.3 — Business Information", "template"),
      sec("section-1-certifications", "1.4 — Certifications", "template"),
      sec("section-1-insurance", "1.5 — Insurance", "template"),
      sec("section-2-bio-todd", "2.1 — Todd Anderson", "template"),
      sec("section-2-bio-sonja", "2.2 — Sonja Anderson", "template"),
      sec("section-3-work-umatilla", "3.1 — City of Umatilla", "template"),
      sec("section-3-work-hillsboro", "3.2 — Hillsboro Public Library", "template"),
      sec("rfp-sec-transmission", "1. Transmission Letter"),
      sec("rfp-sec-approval", "2. Approval of Governing Body"),
      sec("rfp-closing-capacity", "Capacity"),
    ];
    const numbers = buildRfpTabDisplayNumbers(sections);
    expect(sectionListLabel(sections[7], numbers)).toBe("3.1 — City of Umatilla");
    expect(sectionListLabel(sections[9], numbers)).toBe("4. Transmission Letter");
    expect(sectionListLabel(sections[10], numbers)).toBe(
      "5. Approval of Governing Body",
    );
    expect(sectionListLabel(sections[11], numbers)).toBe("6. Capacity");
  });
});
