import { describe, expect, it } from "vitest";
import { rebuildOutlineFromResearch } from "./proposal-draft";
import type { ProposalResearch } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";

const rfp = { id: "rfp-1", pageLimit: 30 } as RfpRecord;

describe("rebuildOutlineFromResearch", () => {
  it("rebuilds a fresh outline without throwing when there is no existing draft", () => {
    const research = {
      rfpSections: [{ id: "section-4-project-approach", title: "Approach" }],
    } as ProposalResearch;

    expect(() => rebuildOutlineFromResearch(rfp, research, null)).not.toThrow();
  });

  it("preserves already-generated dynamic team bio / work sections instead of the placeholder", () => {
    const research = {
      rfpSections: [{ id: "section-4-project-approach", title: "Approach" }],
    } as ProposalResearch;

    const existingDraft = {
      updatedAt: new Date().toISOString(),
      sections: [
        {
          id: "section-2-bio-sonja",
          title: "Sonja",
          wordTarget: 500,
          required: true,
          custom: false,
          content: "Sonja's bio content",
          status: "generated" as const,
          source: "template" as const,
          mode: "select" as const,
        },
      ],
    };

    const rebuilt = rebuildOutlineFromResearch(rfp, research, existingDraft);
    const ids = rebuilt.sections.map((s) => s.id);
    expect(ids).toContain("section-2-bio-sonja");
    expect(ids).not.toContain("section-2-bio-placeholder");
  });
});
