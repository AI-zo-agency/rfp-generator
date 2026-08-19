import { describe, expect, it } from "vitest";
import {
  applyFinancialNavSearch,
  parseFinancialTab,
  parseTeamworkView,
} from "./financial-tab";

describe("parseFinancialTab", () => {
  it("keeps a known sidebar tab", () => {
    expect(parseFinancialTab("teamwork")).toBe("teamwork");
  });

  it("falls back to quickbooks so unknown or missing values cannot crash the page", () => {
    expect(parseFinancialTab(null)).toBe("quickbooks");
    expect(parseFinancialTab("not-a-tab")).toBe("quickbooks");
  });
});

describe("parseTeamworkView", () => {
  it("keeps the inner Projects tab", () => {
    expect(parseTeamworkView("projects")).toBe("projects");
  });

  it("falls back to position", () => {
    expect(parseTeamworkView(null)).toBe("position");
    expect(parseTeamworkView("nope")).toBe("position");
  });
});

describe("applyFinancialNavSearch", () => {
  it("writes teamwork so a refresh can restore that page", () => {
    expect(applyFinancialNavSearch("", { tab: "teamwork" })).toBe("?tab=teamwork");
  });

  it("omits the default QuickBooks tab from the URL", () => {
    expect(applyFinancialNavSearch("tab=teamwork", { tab: "quickbooks" })).toBe("");
  });

  it("writes the inner Projects view and clears it when leaving Teamwork", () => {
    expect(applyFinancialNavSearch("tab=teamwork", { view: "projects" })).toBe(
      "?tab=teamwork&view=projects",
    );
    expect(
      applyFinancialNavSearch("tab=teamwork&view=projects", { tab: "quickbooks" }),
    ).toBe("");
  });
});
