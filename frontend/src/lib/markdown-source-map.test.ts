import { describe, expect, it } from "vitest";
import { findSourceRange, projectMarkdown } from "./markdown-source-map";

const KPI_TABLE = `### Event KPIs

| EVENT KPI | TARGET |
| --- | --- |
| Events conducted per island | [VERIFY: target number] |
| Total attendance | [VERIFY: target number] |
`;

describe("table selections with VERIFY chips", () => {
  it("projects VERIFY tags as the on-screen Confirm before submit label", () => {
    const text = projectMarkdown(KPI_TABLE).text;
    expect(text).toContain("Confirm before submit");
    expect(text).toContain("Events conducted per island");
    expect(text).not.toContain("[VERIFY");
  });

  it("maps a tab/newline table drag (how browsers copy HTML tables)", () => {
    const selected =
      "EVENT KPI\tTARGET\nEvents conducted per island\tConfirm before submit — target number";
    const range = findSourceRange(KPI_TABLE, selected);
    expect(range).not.toBeNull();
    const excerpt = KPI_TABLE.slice(range!.start, range!.end);
    expect(excerpt).toContain("Events conducted per island");
    expect(excerpt).toContain("[VERIFY: target number]");
  });
});
