import { withAuthUserEmail } from "@/lib/auth-user-email";

const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export type ActivityWorkspace = "rfp" | "financial" | "leads";

export interface UserActivityEvent {
  id: string;
  created_at: string;
  workspace: ActivityWorkspace | string;
  actor_email: string;
  action: string;
  outcome?: string;
  entity_type: string | null;
  entity_id: string | null;
  entity_label: string | null;
  summary: string;
  metadata: Record<string, unknown>;
  request_id: string | null;
  run_id: string | null;
}

export interface UserActivityStats {
  events_today: number;
  unique_actors_7d: number;
}

export interface UserActivityResponse {
  workspace: string;
  items: UserActivityEvent[];
  next_cursor: string | null;
  stats: UserActivityStats;
  error?: string;
}

export async function fetchUserActivity(params: {
  workspace: ActivityWorkspace;
  limit?: number;
  cursor?: string | null;
  actor?: string;
  action?: string;
}): Promise<UserActivityResponse> {
  const qs = new URLSearchParams();
  qs.set("workspace", params.workspace);
  qs.set("limit", String(params.limit ?? 50));
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.actor?.trim()) qs.set("actor", params.actor.trim());
  if (params.action?.trim()) qs.set("action", params.action.trim());

  const res = await fetch(`${BACKEND}/api/v1/activity?${qs.toString()}`, {
    cache: "no-store",
    headers: withAuthUserEmail({ Accept: "application/json" }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Activity request failed (${res.status})`);
  }
  return (await res.json()) as UserActivityResponse;
}

export function hrefForActivityEvent(event: UserActivityEvent): string | null {
  const id = event.entity_id?.trim();
  if (!id) return null;
  switch (event.workspace) {
    case "rfp":
      if (event.action.startsWith("proposal.") || event.action.startsWith("kb.")) {
        if (event.action.startsWith("kb.")) return "/knowledge-base";
        return `/proposals?rfp=${encodeURIComponent(id)}`;
      }
      return `/rfps/${encodeURIComponent(id)}`;
    case "financial":
      return "/financial-insights?tab=activity";
    case "leads":
      return "/lead-finder";
    default:
      return null;
  }
}
