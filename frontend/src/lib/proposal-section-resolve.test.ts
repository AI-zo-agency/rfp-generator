import { describe, expect, it } from "vitest";
import {
  messageLooksStructural,
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
    sec("section-1-insurance", "1.5 — Insurance Information"),
    sec("section-2-bio-brian", "2.2 — Brian Niles"),
    sec("section-2-bio-rachel", "2.3 — Rachel Rice"),
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
});
