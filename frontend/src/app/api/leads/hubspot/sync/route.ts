import { backendFetch } from "@/lib/backend-api";

export const runtime = "nodejs";

/** Lead Finder Refresh button → incremental HubSpot sync (only contacts changed since the last run). */
export async function POST() {
  const response = await backendFetch("/leads/hubspot/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "auto" }),
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
