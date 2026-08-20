export type LeadFilter = "All" | "Hot" | "Warm" | "Cool" | "Excluded";
export type EnrichmentStatus = "idle" | "loading" | "ready" | "unavailable";

export function preparationState({
  briefLoaded,
  enrichmentStatus,
}: {
  briefLoaded: boolean;
  enrichmentStatus: EnrichmentStatus;
}): { ready: boolean; label: string } {
  if (!briefLoaded) return { ready: false, label: "Loading research brief…" };
  if (enrichmentStatus === "idle" || enrichmentStatus === "loading") {
    return { ready: false, label: "Verifying company and contact data…" };
  }
  if (enrichmentStatus === "unavailable") {
    return { ready: true, label: "Using research brief; verification unavailable." };
  }
  return { ready: true, label: "Research and verification ready." };
}

export function shouldLoadProspectInputs(alreadyLoaded: boolean): boolean {
  return !alreadyLoaded;
}

export interface PersonEnrichment {
  full_name: string | null;
  job_title: string | null;
  job_title_role: string | null;
  job_title_levels?: string | null;
  job_company_name?: string | null;
  phone: string | null;
  linkedin_url?: string | null;
  confidence: string;
  basis: string;
}

export interface CompanyEnrichment {
  company_name?: string | null;
  industry?: string | null;
  company_type?: string | null;
  city?: string | null;
  state?: string | null;
  employee_band?: string | null;
  employee_count?: number | null;
  founded?: number | string | null;
  inferred_revenue?: string | null;
  linkedin_url?: string | null;
  website?: string | null;
  what_they_do?: string | null;
  tags?: string[] | null;
  confidence?: string;
  basis?: string;
  person?: PersonEnrichment | null;
  person_error?: string | null;
  company_skipped?: string | null;
  company_error?: string | null;
}

export function hrefFor(url: string | null | undefined): string | null {
  const value = url?.trim();
  if (!value) return null;
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

export function enrichmentRows(enrichment: CompanyEnrichment): { label: string; value: string }[] {
  const size =
    enrichment.employee_band ||
    (enrichment.employee_count != null ? `${enrichment.employee_count} employees` : "");
  return (
    [
      ["Company", enrichment.company_name],
      ["Industry", enrichment.industry],
      ["Type", enrichment.company_type],
      ["Location", [enrichment.city, enrichment.state].filter(Boolean).join(", ")],
      ["Size", size],
      ["Founded", enrichment.founded != null ? String(enrichment.founded) : ""],
      ["Revenue", enrichment.inferred_revenue],
      ["What they do", enrichment.what_they_do],
      ["Tags", (enrichment.tags ?? []).filter(Boolean).join(", ")],
    ] as const
  )
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => ({ label, value: String(value) }));
}

export function personRows(person: PersonEnrichment): { label: string; value: string }[] {
  return (
    [
      ["Name", person.full_name],
      ["Title", person.job_title],
      ["Role", person.job_title_role],
      ["Seniority", person.job_title_levels],
      ["Works at", person.job_company_name],
      ["Phone", person.phone],
    ] as const
  )
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => ({ label, value: String(value) }));
}

export function filterLeads<
  T extends {
    name: string | null;
    email: string;
    company: string | null;
    industry: string | null;
    location: string | null;
    band: string;
    disqualified_reason: string | null;
  },
>(leads: T[], query: string, filter: LeadFilter): T[] {
  const term = query.trim().toLowerCase();

  return leads.filter((lead) => {
    const matchesFilter =
      filter === "All" ||
      (filter === "Excluded"
        ? Boolean(lead.disqualified_reason)
        : lead.band === filter && !lead.disqualified_reason);
    const searchable = [lead.name, lead.email, lead.company, lead.industry, lead.location]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return matchesFilter && (!term || searchable.includes(term));
  });
}
