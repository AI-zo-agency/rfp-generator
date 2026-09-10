import { describe, expect, it } from "vitest";
import {
  repairListShapedReferencesMarkdown,
  repairReferenceTableMarkdown,
} from "./reference-table-repair";

describe("repairReferenceTableMarkdown", () => {
  it("rebuilds bullets below header into aligned table rows", () => {
    const body = [
      "| # | Contact Name | Title | Organization | Phone | Email |",
      "| --- | --- | --- | --- | --- | --- |",
      "",
      "- City of Bend — **Needs your input** — verified reference contact from ClientList/KB",
      "- Maricopa County — **Needs your input** — verified reference contact from ClientList/KB",
    ].join("\n");
    const out = repairReferenceTableMarkdown(body);
    expect(out).toContain("Contact Name");
    expect(out).toContain("City of Bend");
    expect(out).toContain("Maricopa County");
    expect(out).toContain("| 1 |");
    expect(out).not.toMatch(/^- City of Bend/m);
  });
});

describe("repairListShapedReferencesMarkdown", () => {
  it("converts numbered list + empty Contact into column-per-reference table", () => {
    const body = [
      "Single 4-column table, one column per reference; no additional layout needed.",
      "",
      "1. City of Umatilla, Oregon, Rock the Locks Music Festival",
      "**Contact:**",
      "",
      "1. Deschutes County, Oregon, County-wide Brand Identity",
      "2. City of Santa Clara, California, Omni-channel Campaign",
      "",
      "[MANUAL FILL: Sonja — verified client references]",
    ].join("\n");
    const out = repairListShapedReferencesMarkdown(body, {
      originalForLayoutHint: body,
    });
    expect(out).toContain("| Field |");
    expect(out).toContain("Reference 1");
    expect(out).toContain("City of Umatilla");
    expect(out).toContain("Deschutes County");
    expect(out).toContain("City of Santa Clara");
    expect(out).not.toMatch(/^1\.\s+City of Umatilla/m);
    expect(out).not.toMatch(/no additional layout needed/i);
    expect(out).not.toMatch(/^\*\*Contact:\*\*$/m);
  });

  it("builds row table when RFP does not ask for column-per-reference", () => {
    const body = [
      "1. City of Bend brand campaign",
      "2. Maricopa County tourism",
    ].join("\n");
    const out = repairListShapedReferencesMarkdown(body);
    expect(out).toContain("| Client / Engagement |");
    expect(out).toContain("City of Bend brand campaign");
    expect(out).toContain("Maricopa County tourism");
  });
});
