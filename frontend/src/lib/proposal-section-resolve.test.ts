import { describe, expect, it } from "vitest";
import {
  messageLooksStructural,
  messageNeedsCaseStudyClarify,
  pinnedSectionConflictsWithMessage,
  resolveChatTarget,
  resolveSectionFromMention,
  sectionPersonName,
} from "./proposal-section-resolve";
import type { OutlineSection } from "@/types/proposal";

function sec(id: string, title: string): OutlineSection {
  return {
    id,
    title,
    content: "x",
    wordTarget: 500,
    required: true,
    custom: false,
    status: "generated",
    source: "template",
  };
}

describe("resolveSectionFromMention", () => {
  const sections = [
    sec("section-1-who-we-are", "1.1 — Who We Are"),
    sec("section-1-insurance", "1.5 — Insurance Information"),
    sec("section-2-bio-brian", "2.2 — Brian Niles"),
    sec("section-2-bio-rachel", "2.3 — Rachel Rice"),
    sec("section-3-work-oregon", "3.1 — Oregon Employment"),
    sec("section-3-work-umatilla", "3.3 — City of Umatilla Digital Campaign 2006"),
  ];

  it("matches person name even when viewing another section", () => {
    const hit = resolveSectionFromMention(
      sections,
      "Instead of Brian Niles bio add Ron Comer bio",
      "section-1-insurance"
    );
    expect(hit?.id).toBe("section-2-bio-brian");
  });

  it("prefers bios over insurance for bio/resume asks", () => {
    const hit = resolveSectionFromMention(
      sections,
      "add another team bio per RFP",
      "section-1-insurance"
    );
    expect(hit?.id.startsWith("section-2-bio-")).toBe(true);
  });

  it("still routes when a case study is named", () => {
    const hit = resolveSectionFromMention(
      sections,
      "rewrite Oregon Employment with more tourism proof",
      "section-1-who-we-are"
    );
    expect(hit?.id).toBe("section-3-work-oregon");
  });

  it("still falls back to viewing section for generic improve", () => {
    const hit = resolveSectionFromMention(
      sections,
      "make this tighter",
      "section-1-insurance"
    );
    expect(hit?.id).toBe("section-1-insurance");
  });

  it("parses person name from title", () => {
    expect(sectionPersonName("2.2 — Brian Niles")).toBe("Brian Niles");
  });

  it("detects structural messages", () => {
    expect(
      messageLooksStructural("Instead of Brian Niles bio add Ron Comer")
    ).toBe(true);
  });

  it("bio pin conflict only — not case-study keywords", () => {
    expect(
      pinnedSectionConflictsWithMessage(
        "add another team bio",
        "section-1-who-we-are"
      )
    ).toBe(true);
    expect(
      pinnedSectionConflictsWithMessage(
        "replace existing case studies from KB",
        "section-1-who-we-are"
      )
    ).toBe(false);
  });
});

describe("resolveChatTarget", () => {
  const sections = [
    sec("section-1-who-we-are", "1.1 — Who We Are"),
    sec("section-2-bio-brian", "2.2 — Brian Niles"),
    sec("section-2-bio-rachel", "2.3 — Rachel Rice"),
    sec("section-3-work-oregon", "3.1 — Oregon Employment"),
    sec("section-3-work-san-leandro", "3.2 — Municipality Summ"),
    sec("section-3-work-umatilla", "3.3 — City of Umatilla Digital Campaign 2006"),
  ];

  it("uses explicit pin with high confidence", () => {
    const pin = sections[0];
    const result = resolveChatTarget(sections, "make this tighter", {
      viewingSectionId: "section-3-work-oregon",
      pinnedSection: pin,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-who-we-are");
      expect(result.confidence).toBe("high");
      expect(result.reason).toBe("pinned");
    }
  });

  it("resolves named section from query", () => {
    const result = resolveChatTarget(
      sections,
      "rewrite 3.1 — Oregon Employment with more tourism proof",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-3-work-oregon");
      expect(result.confidence).toBe("high");
    }
  });

  it("asks to confirm when multiple bios match vaguely", () => {
    const result = resolveChatTarget(sections, "improve the Brian bio wait also Rachel", {
      viewingSectionId: "section-1-who-we-are",
    });
    expect(result?.kind).toBe("clarify");
    if (result?.kind === "clarify") {
      expect(result.candidates.length).toBeGreaterThan(1);
      expect(result.question.toLowerCase()).toContain("which");
    }
  });

  it("asks which Our Work piece — does NOT silently use open Who We Are", () => {
    const msg =
      "you only add best case studies that suit this rfp requirements from knowledge base and replace this 3 existing case studies";
    expect(messageNeedsCaseStudyClarify(msg)).toBe(true);

    const result = resolveChatTarget(sections, msg, {
      viewingSectionId: "section-1-who-we-are",
    });
    expect(result?.kind).toBe("clarify");
    if (result?.kind === "clarify") {
      expect(result.candidates.some((c) => c.id === "section-3-work-oregon")).toBe(
        true
      );
      expect(result.question.toLowerCase()).toContain("won't guess");
      // Open tab may be listed as an option, but we must not auto-resolve to it
      expect(result.candidates[0].id).not.toBe("section-1-who-we-are");
    }
  });

  it("pin still allows editing Who We Are about case-study mentions", () => {
    const result = resolveChatTarget(
      sections,
      "weave better case study mentions into this prose",
      {
        viewingSectionId: "section-1-who-we-are",
        pinnedSection: sections[0],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-who-we-are");
      expect(result.reason).toBe("pinned");
    }
  });
});
