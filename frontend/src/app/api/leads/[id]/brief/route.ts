import { backendFetch } from "@/lib/backend-api";

export const runtime = "nodejs";
export const maxDuration = 60;

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const wantsAi = new URL(request.url).searchParams.get("ai") === "true";
  const response = await backendFetch(
    `/leads/${encodeURIComponent(id)}/brief${wantsAi ? "?ai=true" : ""}`,
    { method: "GET" }
  );
  const body = await response.text();
  return new Response(body, {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
