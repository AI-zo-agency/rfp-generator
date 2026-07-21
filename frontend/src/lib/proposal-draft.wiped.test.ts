import { describe, expect, it } from "vitest";
import { isLikelyWipedOutline } from "./proposal-draft";
import type { ProposalOutline, ProposalResearch } from "@/types/proposal";

function emptyOutline(
  overrides?: Partial<ProposalOutline>
): ProposalOutline {
  return {
    updatedAt: new Date().toISOString(),
    sections: [
      {
        id: "section-1-who-we-are",
        title: "1.1 — Who We Are",
        pageLimit: 1,
        wordTarget: 600,
        required: true,
        custom: false,
        content: "",
        status: "outline",
        source: "template",
        mode: "pull",
      },
      {
        id: "section-1-org-structure",
        title: "1.2 — Organizational Structure",
        pageLimit: 2,
        wordTarget: 800,
        required: true,
        custom: false,
        content: "",
        status: "outline",
        source: "template",
        mode: "pull",
      },
      {
        id: "section-4",
        title: "Section 4 — Project Approach",
        pageLimit: 2,
        wordTarget: 800,
        required: true,
        custom: false,
        content: "",
        status: "outline",
        source: "rfp",
        mode: "write",
      },
    ],
    ...overrides,
  };
}

describe("isLikelyWipedOutline", () => {
  it("blocks empty 7-card shells when research has RFP tabs (old <=5 bug)", () => {
    const research = {
      rfpSections: [
        { id: "section-4", title: "Approach" },
        { id: "section-5", title: "Scope" },
      ],
    } as ProposalResearch;
    expect(isLikelyWipedOutline(emptyOutline(), research)).toBe(true);
  });

  it("blocks empty shells that still carry snapshots", () => {
    expect(
      isLikelyWipedOutline(
        emptyOutline({
          snapshots: [
            {
              savedAt: "2026-01-01T00:00:00Z",
              label: "Before Scan",
              sectionCount: 12,
              sections: [],
            },
          ],
        }),
        null
      )
    ).toBe(true);
  });

  it("allows a true fresh empty draft with no research", () => {
    expect(isLikelyWipedOutline(emptyOutline(), null)).toBe(false);
  });

  it("allows outlines that still have body text", () => {
    const outline = emptyOutline();
    outline.sections[0].content = "We are zö agency.";
    expect(
      isLikelyWipedOutline(outline, {
        rfpSections: [{ id: "section-4", title: "Approach" }],
      } as ProposalResearch)
    ).toBe(false);
  });
});
