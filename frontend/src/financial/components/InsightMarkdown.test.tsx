import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { InsightMarkdown, parseBlocks } from "./InsightMarkdown";

function render(text: string): string {
  return renderToStaticMarkup(createElement(InsightMarkdown, { text }));
}

describe("InsightMarkdown", () => {
  it("renders bullets, bold, and italic", () => {
    const html = render(
      "- **$1,630,713** carried over across *70* items\n- Fix First Call Quality",
    );
    expect(html).toContain("<ul");
    expect(html).toContain("<strong>$1,630,713</strong>");
    expect(html).toContain("<em>70</em>");
    expect(html.match(/<li>/g)?.length).toBe(2);
  });

  it("renders plain paragraphs without lists when only one block", () => {
    const html = render("One sentence. Another sentence.");
    expect(html).not.toContain("<ul");
    expect(html).toContain("<p");
    expect(html).toContain("One sentence. Another sentence.");
  });

  it("promotes blank-line paragraphs into bullets for skim", () => {
    const blocks = parseBlocks(
      "**Carryover.** Seventy items aged.\n\n**Billing risk.** Orphans at $891,493.63.",
    );
    expect(blocks).toEqual([
      {
        kind: "ul",
        items: [
          "**Carryover.** Seventy items aged.",
          "**Billing risk.** Orphans at $891,493.63.",
        ],
      },
    ]);
    const html = render(
      "**Carryover.** Seventy items aged.\n\n**Billing risk.** Orphans at $891,493.63.",
    );
    expect(html.match(/<li>/g)?.length).toBe(2);
  });
});
