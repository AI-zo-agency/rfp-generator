import { withAuthUserEmail } from "@/lib/auth-user-email";

const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export interface LedgerRow {
  rfp_id: string;
  title: string;
  deleted: boolean;
  deleted_by: string | null;
  deleted_at: string | null;
  ran_by: string[];
  last_touched_by: string | null;
  last_touched_at: string | null;
  last_opened_by: string | null;
  last_opened_at: string | null;
  opened_by_count: number;
  run_count: number;
  cost_usd: number;
  tweak_count: number;
  sections_touched: number;
  words_added: number;
  words_removed: number;
  manual_count: number;
  ai_count: number;
  other_count: number;
  restore_count: number;
}

export interface LedgerPhase {
  phase: string;
  started_at: string;
  finished_at: string | null;
  outcome: string;
}

export interface LedgerRun {
  run_id: string;
  kind: "proposal" | "gonogo";
  owner: string;
  started_at: string;
  finished_at: string | null;
  outcome: "running" | "completed" | "failed" | "cancelled";
  duration_sec: number | null;
  cost_usd: number;
  phases: LedgerPhase[];
  stopped_by: string | null;
}

export interface TweakSummary {
  id: string;
  created_at: string;
  updated_at: string;
  rfp_id: string;
  rfp_title: string;
  section_id: string;
  section_title: string;
  actor_email: string;
  source: string;
  bucket: string;
  kind: string;
  has_instruction: boolean;
  words_before: number;
  words_after: number;
  words_added: number;
  words_removed: number;
  before_title: string;
  after_title: string;
}

export interface TweakDetail extends TweakSummary {
  instruction: string;
  before_text: string;
  after_text: string;
}

export interface LedgerViewer {
  actor_email: string;
  last_opened_at: string;
  open_count: number;
}

export interface LedgerOpen {
  id: string;
  actor_email: string;
  created_at: string;
}

export interface LedgerDetail {
  rfp: LedgerRow | null;
  runs: LedgerRun[];
  tweaks: TweakSummary[];
  viewers: LedgerViewer[];
  opens: LedgerOpen[];
}

export interface LedgerPerson {
  actor_email: string;
  rfps_ran: number;
  rfps_tweaked: number;
  rfps_opened: number;
  rfps_deleted: number;
  runs: number;
  tweaks: number;
  cost_usd: number;
}

async function getJson<T>(path: string, params: URLSearchParams): Promise<T> {
  const qs = params.toString();
  const res = await fetch(`${BACKEND}/api/v1/activity${path}${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
    headers: withAuthUserEmail({ Accept: "application/json" }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Ledger request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export type DeletedFilter = "all" | "hide" | "only";

export async function fetchLedger(params: {
  person?: string;
  from?: string;
  to?: string;
}): Promise<{ items: LedgerRow[]; truncated: boolean }> {
  const qs = new URLSearchParams();
  if (params.person?.trim()) qs.set("person", params.person.trim());
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  return getJson("/rfps", qs);
}

export async function fetchLedgerDetail(
  rfpId: string,
  includeOpens: boolean,
): Promise<LedgerDetail> {
  const qs = new URLSearchParams();
  if (includeOpens) qs.set("include_opens", "true");
  return getJson(`/rfps/${encodeURIComponent(rfpId)}`, qs);
}

export async function fetchPeople(params: {
  from?: string;
  to?: string;
}): Promise<{ items: LedgerPerson[] }> {
  const qs = new URLSearchParams();
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  return getJson("/people", qs);
}

export async function fetchTweakDetail(id: string): Promise<TweakDetail> {
  return getJson(`/tweaks/${encodeURIComponent(id)}`, new URLSearchParams());
}
