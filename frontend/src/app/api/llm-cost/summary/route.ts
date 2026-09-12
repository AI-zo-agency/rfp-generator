import { backendFetch } from "@/lib/backend-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
/** Cap so Analytics never hangs the Next server for minutes. */
export const maxDuration = 45;

export async function GET() {
  try {
    const response = await backendFetch("/llm-cost/summary", {
      method: "GET",
      signal: AbortSignal.timeout(40_000),
    });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "summary unavailable";
    return Response.json(
      { error: message, detail: "LLM cost summary timed out or backend unreachable" },
      { status: 504 },
    );
  }
}
