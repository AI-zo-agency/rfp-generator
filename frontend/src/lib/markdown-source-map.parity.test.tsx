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

  "a KPI table with VERIFY chips": `### Event KPIs

| EVENT KPI | TARGET |
| --- | --- |
| Events conducted per island | [VERIFY: target number] |
| Total attendance | [VERIFY: target number] |
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

  it("maps a browser table selection (tabs/newlines) including VERIFY chips", () => {
    const source = FIXTURES["a KPI table with VERIFY chips"];
    const selected =
      "EVENT KPI\tTARGET\nEvents conducted per island\tConfirm before submit — target number";
    const range = findSourceRange(source, selected);
    expect(range).not.toBeNull();
    expect(source.slice(range!.start, range!.end)).toContain("Events conducted per island");
    expect(source.slice(range!.start, range!.end)).toContain("[VERIFY: target number]");
  });
});
