import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  injectCitationMarkers,
  MarkdownReportBody,
} from "../components/MarkdownReportBody";
import type { EvidenceItem } from "../types/proposal";

const CORPUS: EvidenceItem[] = [
  {
    id: "E12",
    source: "01_companyfacts verified",
    excerpt: "zö agency was founded in 2013 in Bend, Oregon.",
  },
];

describe("post-hoc citation map badges", () => {
  it("injects [E#] after grounded claim text", () => {
    const body = "zö agency was founded in 2013 and has served clients since.";
    const out = injectCitationMarkers(body, [
      { text: body, evidenceIds: ["E12"] },
    ]);
    expect(out).toContain("[E12]");
    expect(out.indexOf("[E12]")).toBeGreaterThan(body.indexOf("2013"));
  });

  it("renders badge from citationMap without inline markers in source", () => {
    const claim = "zö agency was founded in 2013 and has served clients since.";
    const html = renderToStaticMarkup(
      <MarkdownReportBody
        body={claim}
        variant="document"
        evidenceCorpus={CORPUS}
        citationMap={[{ text: claim, evidenceIds: ["E12"], method: "overlap" }]}
      />,
    );
    expect(html).toContain("evidence-cite-badge");
    expect(html).toContain('aria-label="Evidence E12:');
  });
});
