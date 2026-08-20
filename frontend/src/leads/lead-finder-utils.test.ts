import { describe, expect, it } from "vitest";
import {
  enrichmentRows,
  filterLeads,
  hrefFor,
  personRows,
  preparationState,
  shouldLoadProspectInputs,
} from "./lead-finder-utils";

const leads = [
  {
    id: "1",
    name: "Megan",
    email: "megan@example.com",
    company: "City of Sacramento",
    industry: "Government",
    location: "Sacramento, CA",
    band: "Hot",
    disqualified_reason: null,
  },
  {
    id: "2",
    name: "Patti",
    email: "patti@example.com",
    company: "Community Action",
    industry: "Government",
    location: null,
    band: "Warm",
    disqualified_reason: null,
  },
];

describe("filterLeads", () => {
  it("matches a search term and a selected band", () => {
    expect(filterLeads(leads, "city", "Hot").map((lead) => lead.id)).toEqual(["1"]);
  });
});

describe("enrichmentRows", () => {
  it("omits empty Monid fields and prefixes a bare LinkedIn host", () => {
    expect(hrefFor("linkedin.com/company/acme")).toBe("https://linkedin.com/company/acme");
    expect(
      enrichmentRows({
        company_name: "Mt Baker Products",
        industry: "wholesale",
        company_type: "private",
        city: "bellingham",
        state: "washington",
        employee_band: "51-200",
        employee_count: 84,
        founded: 1978,
        inferred_revenue: "$10M-$25M",
        linkedin_url: "linkedin.com/company/mtbaker",
        website: "mtbakerproducts.com",
        what_they_do: "Wholesale forest products.",
        tags: ["timber"],
        confidence: "medium",
        basis: "Monid match",
      }).map((row) => row.label),
    ).toEqual(["Company", "Industry", "Type", "Location", "Size", "Founded", "Revenue", "What they do", "Tags"]);
    expect(
      personRows({
        full_name: "Sam King",
        job_title: "Purchasing Manager",
        job_title_role: "operations",
        job_title_levels: "manager",
        job_company_name: "Mt Baker Products",
        phone: "+13605550199",
        linkedin_url: "linkedin.com/in/samking",
        confidence: "high",
        basis: "Monid person match",
      }).map((row) => row.label),
    ).toEqual(["Name", "Title", "Role", "Seniority", "Works at", "Phone"]);
  });
});

describe("preparationState", () => {
  it("waits only while enrichment is in flight", () => {
    expect(preparationState({ briefLoaded: true, enrichmentStatus: "loading" })).toEqual({
      ready: false,
      label: "Verifying company and contact data…",
    });
  });

  it("allows preparation before enrichment is requested", () => {
    expect(preparationState({ briefLoaded: true, enrichmentStatus: "idle" })).toEqual({
      ready: true,
      label: "Research brief ready. Enrichment is optional.",
    });
  });

  it("allows preparation after verification is unavailable", () => {
    expect(preparationState({ briefLoaded: true, enrichmentStatus: "unavailable" })).toEqual({
      ready: true,
      label: "Using research brief; verification unavailable.",
    });
  });

  it("starts prospect inputs only once", () => {
    expect(shouldLoadProspectInputs(false)).toBe(true);
    expect(shouldLoadProspectInputs(true)).toBe(false);
  });
});
