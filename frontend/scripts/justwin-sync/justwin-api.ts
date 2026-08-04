import type { Page } from "playwright";
import type { JustWinLead } from "./types";
import { getJustWinBaseUrl } from "./browser";

const API_ROOT = process.env.JUSTWIN_API_ROOT ?? "https://api.justwin.ai";
const PAGE_SIZE = 100;

/** JustWin's own tab identifiers, as used by the `lifecycle_state` query param. */
export const LIFECYCLE_STATES = ["hot", "warm", "review"] as const;
export type LifecycleState = (typeof LIFECYCLE_STATES)[number];

export interface JustWinApiClient {
  page: Page;
  headers: Record<string, string>;
  companyId: string;
}

interface RawLead {
  id: string;
  target?: string;
  created?: string;
  due_date?: string | null;
  lifecycle_state?: string;
  documentless?: boolean;
  state?: { abbreviation?: string } | null;
  readonly_values?: {
    name?: string;
    relevance_score_integer?: number;
    insights?: { summary?: string; title?: string; due_date?: string };
  } | null;
}

interface LeadPage {
  count: number;
  next: string | null;
  results: RawLead[];
}

/**
 * The lead list is rendered from `created`, which JustWin's UI formats in UTC.
 * Matching on the UTC date keeps "Posted Aug 4" in the dashboard equal to
 * picking Aug 4 in the sync modal.
 */
export function postedDateOf(lead: RawLead): string {
  if (!lead.created) return "";
  const parsed = new Date(lead.created);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toISOString().slice(0, 10);
}

export async function createApiClient(page: Page): Promise<JustWinApiClient> {
  const token = await page.evaluate(() => localStorage.getItem("token"));
  if (!token) {
    throw new Error(
      "JustWin auth token not found — delete data/justwin-session.json and rerun sync"
    );
  }
  const headers = { Authorization: `Bearer ${token}` };

  const companiesRes = await page.request.get(`${API_ROOT}/companies`, { headers });
  if (!companiesRes.ok()) {
    throw new Error(`JustWin companies API failed (${companiesRes.status()})`);
  }
  const companies = (await companiesRes.json()) as { results?: { id: string }[] };
  const companyId = companies.results?.[0]?.id;
  if (!companyId) {
    throw new Error("JustWin companies API returned no company");
  }

  return { page, headers, companyId };
}

function toLead(raw: RawLead, tab: LifecycleState): JustWinLead {
  const readonly = raw.readonly_values ?? {};
  const title = readonly.name ?? readonly.insights?.title ?? "Untitled solicitation";

  return {
    externalId: raw.id,
    title,
    location: raw.state?.abbreviation ?? "",
    postedDate: postedDateOf(raw),
    // JustWin already gives an ISO due date; no PDF parsing required.
    dueDate: raw.due_date ?? "",
    score: readonly.relevance_score_integer ?? 0,
    description: readonly.insights?.summary ?? title,
    detailUrl: `${getJustWinBaseUrl()}/leads/${raw.id}/summary`,
    tab,
  };
}

/**
 * Fetch every lead in a tab, newest first, following pagination.
 *
 * When `targetDate` is set we can stop early: results are ordered by `-created`,
 * so once a page starts returning leads older than the target there is nothing
 * left to match.
 */
export async function fetchLeadsForTab(
  client: JustWinApiClient,
  tab: LifecycleState,
  targetDate?: string
): Promise<JustWinLead[]> {
  const leads: JustWinLead[] = [];
  let url =
    `${API_ROOT}/leads?company=${client.companyId}&assigned=true` +
    `&page_size=${PAGE_SIZE}&ordering=-created&jurisdiction=all&lifecycle_state=${tab}&page=1`;
  let pages = 0;

  while (url) {
    const res = await client.page.request.get(url, { headers: client.headers });
    if (!res.ok()) {
      throw new Error(`JustWin leads API failed for "${tab}" (${res.status()})`);
    }
    const body = (await res.json()) as LeadPage;
    pages++;

    let olderThanTarget = false;
    for (const raw of body.results ?? []) {
      const posted = postedDateOf(raw);
      if (targetDate) {
        if (posted && posted < targetDate) {
          olderThanTarget = true;
          continue;
        }
        if (posted !== targetDate) continue;
      }
      leads.push(toLead(raw, tab));
    }

    if (targetDate && olderThanTarget) break;
    url = body.next ?? "";
  }

  console.log(
    `[justwin-sync] ${tab}: ${leads.length} lead(s)` +
      `${targetDate ? ` posted ${targetDate}` : ""} (${pages} page(s) scanned)`
  );
  return leads;
}

/** Resolve the signed S3 URL for a lead's solicitation document. */
export async function resolvePdfUrl(
  client: JustWinApiClient,
  leadId: string
): Promise<string | null> {
  const leadRes = await client.page.request.get(`${API_ROOT}/leads/${leadId}`, {
    headers: client.headers,
  });
  if (!leadRes.ok()) return null;

  const lead = (await leadRes.json()) as { target?: string; documentless?: boolean };
  if (lead.documentless || !lead.target) return null;

  const viewRes = await client.page.request.get(
    `${API_ROOT}/targets/${lead.target}/view`,
    { headers: client.headers }
  );
  if (!viewRes.ok()) return null;

  const payload = (await viewRes.json()) as { url?: string };
  return payload.url ?? null;
}
