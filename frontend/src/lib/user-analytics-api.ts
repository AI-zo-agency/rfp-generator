import { withAuthUserEmail } from "@/lib/auth-user-email";
import type { AnalyticsWorkspace } from "@/lib/zo-analytics";

const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export interface AnalyticsSummary {
  workspace: string;
  from: string;
  to: string;
  store?: string;
  error?: string;
  time: { visible_ms: number; engaged_ms: number };
  top_pages: { path: string; views: number }[];
  top_features: { feature: string; clicks: number; unique_users: number }[];
  by_user: {
    actor_email: string;
    visible_ms: number;
    engaged_ms: number;
    page_views: number;
    clicks: number;
  }[];
  funnels: {
    id: string;
    label: string;
    steps: { id: string; users: number }[];
  }[];
}

export async function fetchAnalyticsSummary(params: {
  workspace: AnalyticsWorkspace;
  from?: string;
  to?: string;
  actor?: string;
}): Promise<AnalyticsSummary> {
  const qs = new URLSearchParams();
  qs.set("workspace", params.workspace);
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.actor?.trim()) qs.set("actor", params.actor.trim());

  const res = await fetch(`${BACKEND}/api/v1/analytics/summary?${qs}`, {
    cache: "no-store",
    headers: withAuthUserEmail({ Accept: "application/json" }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Analytics summary failed (${res.status})`);
  }
  return (await res.json()) as AnalyticsSummary;
}

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem ? `${m}m ${rem}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}
