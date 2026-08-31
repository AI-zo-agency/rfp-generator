import { describe, expect, it } from "vitest";
import {
  applyFinancialNavSearch,
  parseAgencyView,
  parseFinancialTab,
  parseTeamworkView,
} from "./financial-tab";

describe("parseFinancialTab", () => {
  it("keeps a known sidebar tab", () => {
    expect(parseFinancialTab("teamwork")).toBe("teamwork");
    expect(parseFinancialTab("agency")).toBe("agency");
  });

  it("falls back to quickbooks so unknown or missing values cannot crash the page", () => {
    expect(parseFinancialTab(null)).toBe("quickbooks");
    expect(parseFinancialTab("not-a-tab")).toBe("quickbooks");
  });

  it("maps legacy ai tab URLs to iworker", () => {
    expect(parseFinancialTab("ai")).toBe("iworker");
  });
});

describe("parseAgencyView", () => {
  it("keeps agency job tabs and mapping, defaulting unknown values to Queue", () => {
    expect(parseAgencyView("jobs")).toBe("jobs");
    expect(parseAgencyView("portfolio")).toBe("portfolio");
    expect(parseAgencyView("invoices")).toBe("invoices");
    expect(parseAgencyView("orphans")).toBe("orphans");
    expect(parseAgencyView("mapping")).toBe("mapping");
    expect(parseAgencyView(null)).toBe("jobs");
    expect(parseAgencyView("nope")).toBe("jobs");
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

  it("writes Agency views, omits Queue default, and clears incompatible views", () => {
    expect(applyFinancialNavSearch("", { tab: "agency" })).toBe("?tab=agency");
    expect(applyFinancialNavSearch("tab=agency", { view: "portfolio" })).toBe(
      "?tab=agency&view=portfolio",
    );
    expect(applyFinancialNavSearch("tab=agency", { view: "mapping" })).toBe(
      "?tab=agency&view=mapping",
    );
    expect(applyFinancialNavSearch("tab=agency&view=mapping", { view: "jobs" })).toBe(
      "?tab=agency",
    );
    expect(
      applyFinancialNavSearch("tab=agency&view=mapping", { tab: "teamwork" }),
    ).toBe("?tab=teamwork");
    expect(
      applyFinancialNavSearch("tab=teamwork&view=projects", { tab: "agency" }),
    ).toBe("?tab=agency");
  });
});
