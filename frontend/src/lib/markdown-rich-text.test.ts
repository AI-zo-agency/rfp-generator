import { describe, expect, it } from "vitest";
import {
  htmlToMarkdown,
  markdownToHtml,
  normalizeMarkdownForCompare,
  repairLeakedHtmlInMarkdown,
} from "./markdown-rich-text";

describe("markdown-rich-text", () => {
  it("converts TipTap table HTML (colgroup + cell paragraphs) to GFM pipes", () => {
    const tipTapHtml = `<table class="proposal-rich-table" style="min-width: 75px;">
<colgroup><col style="min-width: 25px;"><col style="min-width: 25px;"><col style="min-width: 25px;"></colgroup>
<tbody>
<tr><th colspan="1" rowspan="1"><p>Task ID</p></th><th colspan="1" rowspan="1"><p>Task</p></th><th colspan="1" rowspan="1"><p>Investment</p></th></tr>
<tr><td colspan="1" rowspan="1"><p>A1</p></td><td colspan="1" rowspan="1"><p>Discovery</p></td><td colspan="1" rowspan="1"><p>$4,800</p></td></tr>
</tbody></table>`;
    const md = htmlToMarkdown(tipTapHtml);
    expect(md).not.toMatch(/<table/i);
    expect(md).toContain("Task ID");
    expect(md).toContain("$4,800");
    expect(md).toMatch(/\|/);
  });

  it("strips empty nested tables inside cells when converting", () => {
    const tipTapHtml = `<table><tbody>
<tr><th><p>A</p></th><th><p>B</p></th></tr>
<tr><td><p>1</p></td><td><p>2</p><table><tbody><tr><td><p></p></td><td><p></p></td></tr></tbody></table></td></tr>
</tbody></table>`;
    const md = htmlToMarkdown(tipTapHtml);
    expect(md).not.toMatch(/<table/i);
    expect(md).toContain("1");
    expect(md).toContain("2");
  });

  it("repairs markdown that already leaked raw TipTap HTML tables", () => {
    const leaked = `## Pricing

<table class="proposal-rich-table"><tbody><tr><th><p>Item</p></th><th><p>Cost</p></th></tr><tr><td><p>A1</p></td><td><p>$10</p></td></tr></tbody></table>`;
    const repaired = repairLeakedHtmlInMarkdown(leaked);
    expect(repaired).not.toMatch(/<table/i);
    expect(repaired).toContain("Item");
    expect(repaired).toContain("$10");
    expect(markdownToHtml(repaired)).toMatch(/<table/i);
  });

  it("repairs pipe rows corrupted with leftover td/tr HTML fragments", () => {
    const leaked = `| Task ID | Task / Deliverable | Investment |
| --- | --- | --- |
| A1 | Brand book and brand style guide. | $4,800</td></tr><tr><td><p>A2</p></td><td><p>Program logo with color palette and stationery.</p></td><td style="text-align: right;"><p>$2,400</p></td></tr><tr><td><p>A3</p></td><td><p><strong>Website</strong></p></td><td><p>$10,000</p></td></tr>`;
    const repaired = repairLeakedHtmlInMarkdown(leaked);
    expect(repaired).not.toMatch(/<\/?t[rdh]\b/i);
    expect(repaired).not.toMatch(/<p>/i);
    expect(repaired).toContain("A2");
    expect(repaired).toContain("Program logo");
    expect(repaired).toContain("$2,400");
    expect(repaired).toContain("Website");
    expect(repaired.split("\n").filter((l) => l.includes("|")).length).toBeGreaterThanOrEqual(4);
  });

  it("round-trips paragraphs, bold, and headings", () => {
    const md = `## Cost proposal

This is **bold** work for the client.`;
    const html = markdownToHtml(md);
    expect(html).toMatch(/<h2>/i);
    expect(html).toMatch(/<strong>bold<\/strong>/i);
    const back = htmlToMarkdown(html);
    expect(normalizeMarkdownForCompare(back)).toBe(
      normalizeMarkdownForCompare(md),
    );
  });

  it("round-trips GFM tables with header and body rows", () => {
    const md = `| Item | Amount |
| --- | --- |
| Discovery | $10,000 |
| Delivery | $20,000 |`;
    const html = markdownToHtml(md);
    expect(html).toMatch(/<table>/i);
    expect(html).toMatch(/Discovery/i);
    const back = htmlToMarkdown(html);
    expect(back).toContain("Discovery");
    expect(back).toContain("$10,000");
    expect(back).toMatch(/\|/);
    expect(normalizeMarkdownForCompare(back)).toContain("delivery");
  });

  it("preserves MANUAL FILL tags as plain text", () => {
    const md =
      "Rate: [MANUAL FILL: Sonja — confirm on COI] for excess liability.";
    const back = htmlToMarkdown(markdownToHtml(md));
    expect(back).toContain("[MANUAL FILL: Sonja — confirm on COI]");
  });

  it("round-trips bullet lists", () => {
    const md = `- Invoices monthly
- Hourly rate $275
- Materials billed at cost`;
    const back = htmlToMarkdown(markdownToHtml(md));
    expect(normalizeMarkdownForCompare(back)).toContain("invoices monthly");
    expect(normalizeMarkdownForCompare(back)).toContain("hourly rate $275");
  });

  it("keeps blank table body rows through markdown ↔ html", () => {
    const md = `| A | B |
| --- | --- |
| 1 | 2 |
|  |  |
| 3 | 4 |`;
    const back = htmlToMarkdown(markdownToHtml(md));
    const pipeLines = back.split("\n").filter((l) => l.includes("|"));
    expect(pipeLines.length).toBeGreaterThanOrEqual(5);
    expect(back).toMatch(/\|\s*\|\s*\|/);
  });
});
