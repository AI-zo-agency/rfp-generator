import { describe, expect, it } from "vitest";
import { repairReferenceTableMarkdown } from "./reference-table-repair";

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
