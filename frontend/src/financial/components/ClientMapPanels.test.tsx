import { describe, expect, it } from "vitest";

import { mappingHandoffError } from "./ClientMapPanels";
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
