import { describe, expect, it } from "vitest";

import {
  filterClientMapRows,
  mappingHandoffError,
  nextClientNameSort,
  paginateClientMapRows,
  sortClientMapRows,
} from "./ClientMapPanels";
import type { ClientMapRow } from "../types/client-map";

const mappedClient: ClientMapRow = {
  id: "client-1",
  tag_code: "ACME",
  client_name: "Acme",
  qb_customer_ids: ["42"],
  qb_customer_names: ["Acme Co."],
  teamwork_company_ids: [],
  teamwork_company_names: [],
  city: null,
  state: null,
  current_am: null,
  status: "Active",
  source: null,
  highest_value: null,
  is_internal: false,
  link_confidence: "confirmed",
  link_reason: null,
  notes: null,
};

describe("mappingHandoffError", () => {
  it("requires Agency workspace context instead of asking for an opaque site ID", () => {
    expect(mappingHandoffError({ projectId: "7", siteId: null, client: mappedClient }))
      .toMatch("Teamwork workspace context");
  });

  it("explains when the selected client is not mapped to a QuickBooks customer", () => {
    expect(mappingHandoffError({
      projectId: "7",
      siteId: "agency.teamwork.com",
      client: { ...mappedClient, qb_customer_ids: [], qb_customer_names: [] },
    })).toMatch("no QuickBooks customer mapping");
  });

  it("allows a fully resolved project, workspace, and mapped client", () => {
    expect(mappingHandoffError({ projectId: "7", siteId: "agency.teamwork.com", client: mappedClient })).toBeNull();
  });
});

describe("mapping review queue", () => {
  const rows: ClientMapRow[] = [
    mappedClient,
    { ...mappedClient, id: "client-2", tag_code: "NORTH", client_name: "North Star", status: "Paused", link_confidence: "suggested" },
    { ...mappedClient, id: "client-3", tag_code: "ORBIT", client_name: "Orbit", status: null, link_confidence: "unmatched" },
  ];

  it("searches across client and connected company names while retaining the review filter", () => {
    expect(filterClientMapRows(rows, "north", "suggested").map((row) => row.id)).toEqual(["client-2"]);
    expect(filterClientMapRows(rows, "acme co", "all").map((row) => row.id)).toEqual(["client-1", "client-2", "client-3"]);
  });

  it("filters by selected statuses and sorts client names", () => {
    expect(filterClientMapRows(rows, "", "all", ["Paused", "(blank)"]).map((row) => row.id)).toEqual(["client-2", "client-3"]);
    expect(sortClientMapRows(rows, "asc").map((row) => row.client_name)).toEqual(["Acme", "North Star", "Orbit"]);
    expect(sortClientMapRows(rows, "desc").map((row) => row.client_name)).toEqual(["Orbit", "North Star", "Acme"]);
    expect(nextClientNameSort(null)).toBe("asc");
    expect(nextClientNameSort("asc")).toBe("desc");
    expect(nextClientNameSort("desc")).toBeNull();
  });

  it("bounds the page and returns only its review slice", () => {
    const result = paginateClientMapRows(rows, 9, 2);
    expect(result.page).toBe(2);
    expect(result.pageCount).toBe(2);
    expect(result.rows.map((row) => row.id)).toEqual(["client-3"]);
  });
});
