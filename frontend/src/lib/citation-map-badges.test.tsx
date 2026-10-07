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

  it("wires long Pricing Book excerpts onto the badge (tooltip portal not in SSR html)", () => {
    const long =
      "Pricing Book v2: task A1 is not a catalog SKU — sell price $3,700 from the pricing plan engine "
      + "(Pricing Internal roles/POs + book settings). Basis: Scaled from the Pricing Book's Brand Style "
      + "Guide scope and the task library's brand guidance tasks, sized up for a multi-department municipal rollout.";
    expect(long.length).toBeGreaterThan(280);
    const claim = "Brand guidelines $3,700.";
    const html = renderToStaticMarkup(
      <MarkdownReportBody
        body={claim}
        variant="document"
        evidenceCorpus={[{ id: "E33", source: "Pricing Book v2 · custom build", excerpt: long }]}
        citationMap={[{ text: "$3,700", evidenceIds: ["E33"], method: "verbatim" }]}
      />,
    );
    // Closed tooltip content is portaled — SSR only exposes the trigger + source label.
    expect(html).toContain('aria-label="Evidence E33: Pricing Book v2 · custom build"');
    expect(html).toContain("evidence-cite-badge");
  });
});
