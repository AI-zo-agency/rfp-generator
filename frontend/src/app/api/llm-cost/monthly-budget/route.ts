import { backendFetch } from "@/lib/backend-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const response = await backendFetch("/llm-cost/monthly-budget", { method: "GET" });
  const body = await response.text();
  return new Response(body, {
    status: response.status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
