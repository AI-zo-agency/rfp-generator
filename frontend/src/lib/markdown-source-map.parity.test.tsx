import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownReportBody, stripEvidenceCitations } from "../components/MarkdownReportBody";
import { findSourceRange, projectMarkdown } from "./markdown-source-map";

/**
 * The invariant that makes the "Revise content" pill work: projectMarkdown()
 * must produce exactly the text MarkdownReportBody puts on screen. If the two
 * drift, preview selections stop resolving to source offsets and the pill
 * silently stops appearing — which is the bug this module was written to fix.
 *
 * Comparing against the real renderer (rather than a hand-written expectation)
 * is what catches drift when MarkdownReportBody changes.
 */
function renderedText(source: string): string {
  const html = renderToStaticMarkup(
    <MarkdownReportBody body={stripEvidenceCitations(source)} variant="report" />
  );
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

function projectedText(source: string): string {
  return projectMarkdown(source).text.replace(/\s+/g, " ").trim();
}

const FIXTURES: Record<string, string> = {
  "bold labels and an evidence citation": `## Business Information

**Legal Name:** Z'Onion Creative Group LLC

**Doing Business As:** zö agency

**Federal Employer Identification Number (EIN):** 47-4333943 [E12]
`,

  "a table": `### Rate Card

| Role | Rate |
| --- | --- |
| Creative Director | $185 |
| Senior Designer | $140 |
`,

  "lists and a wrapped paragraph": `zö agency has delivered brand and digital work for public agencies
across the Pacific Northwest since 2013.

- Bend, OR headquarters
- 35 full-time employees

1. Discovery
2. Design
`,

  "citation lists and a references line": `Our team holds current certifications [E4, E5, E6].

**References:** E4, E5, E6
`,

  "a thematic break and a blockquote": `Overview

---

> All work is performed in-house.
`,
};

describe("projection parity with MarkdownReportBody", () => {
  for (const [name, source] of Object.entries(FIXTURES)) {
    it(`matches the rendered preview for ${name}`, () => {
      expect(projectedText(source)).toBe(renderedText(source));
    });
  }

  it("resolves any sentence the preview displays back to source offsets", () => {
    for (const source of Object.values(FIXTURES)) {
      const visible = renderedText(source);
      // Slide a window over the rendered text; every window a user could
      // plausibly drag across must map back to a real range.
      const words = visible.split(" ");
      for (let i = 0; i + 4 <= words.length; i += 1) {
        const selected = words.slice(i, i + 4).join(" ");
        expect(findSourceRange(source, selected), `no range for "${selected}"`).not.toBeNull();
      }
    }
  });
});
