import { backendFetch } from "@/lib/backend-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
/** Cap so Analytics never hangs the Next server for minutes. */
export const maxDuration = 45;

/**
 * All-time LLM cost summary proxy — temporarily stubbed.
 * Analytics loads `/api/llm-cost/monthly-budget` instead.
 * To re-enable, restore the backendFetch("/llm-cost/summary") path below.
 */
export async function GET() {
  // try {
  //   const response = await backendFetch("/llm-cost/summary", {
  //     method: "GET",
  //     signal: AbortSignal.timeout(40_000),
  //   });
  //   const body = await response.text();
  //   return new Response(body, {
  //     status: response.status,
  //     headers: {
  //       "Content-Type": "application/json",
  //       "Cache-Control": "no-store",
  //     },
  //   });
  // } catch (error) {
  //   const message = error instanceof Error ? error.message : "summary unavailable";
  //   return Response.json(
  //     { error: message, detail: "LLM cost summary timed out or backend unreachable" },
  //     { status: 504 },
  //   );
  // }

  // Proxy the lightweight monthly budget so any leftover callers still work.
  try {
    const response = await backendFetch("/llm-cost/monthly-budget", {
      method: "GET",
      signal: AbortSignal.timeout(30_000),
    });
    const monthly = (await response.json()) as Record<string, unknown>;
    return Response.json(
      {
        total_cost_usd: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        call_count: 0,
        proposal_count: 0,
        unattributed_cost_usd: 0,
        unknown_node_cost_usd: 0,
        unknown_node_calls: 0,
        unknown_breakdown: { by_model: [], by_date: [] },
        by_proposal: [],
        by_node: [],
        by_model: [],
        monthly_budget: monthly,
        all_time_summary_disabled: true,
      },
      {
        status: 200,
        headers: { "Cache-Control": "no-store" },
      }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "summary unavailable";
    return Response.json(
      { error: message, detail: "Monthly budget unavailable", all_time_summary_disabled: true },
      { status: 504 }
    );
  }
}
