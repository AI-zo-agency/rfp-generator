import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownReportBody } from "../components/MarkdownReportBody";
import type { EvidenceItem } from "../types/proposal";

const CORPUS: EvidenceItem[] = [
  {
    id: "E3",
    source: "00_guide_pricing — Pricing Book",
    excerpt: "Senior Strategist — $185/hour (Catalog STR-185)",
  },
  {
    id: "E7",
    source: "04_Bio — Team bios",
    excerpt: "Ron leads account management with 15+ years.",
  },
];

describe("MarkdownReportBody evidence citation badges", () => {
  it("renders numbered badge markup for [E#] when corpus is provided", () => {
    const html = renderToStaticMarkup(
      <MarkdownReportBody
        body={"Discovery is $185/hr [E3] with Ron leading [E7]."}
        variant="document"
        evidenceCorpus={CORPUS}
      />
    );
    expect(html).toContain("evidence-cite-badge");
    expect(html).toContain("aria-label=\"Evidence E3:");
    expect(html).toContain("aria-label=\"Evidence E7:");
    expect(html).toContain("$185/hr");
    // Visible label is the number; [E3] remains only in sr-only for a11y.
    expect(html).toMatch(/sr-only">\[E3\]/);
    expect(html.replace(/<span class="sr-only">\[E\d+\]<\/span>/g, "")).not.toContain("[E3]");
  });

  it("strips [E#] on document variant when corpus is omitted", () => {
    const html = renderToStaticMarkup(
      <MarkdownReportBody
        body={"Discovery is $185/hr [E3]."}
        variant="document"
      />
    );
    expect(html).not.toContain("evidence-cite-badge");
    expect(html).not.toContain("[E3]");
    expect(html).toContain("$185/hr");
  });
});
