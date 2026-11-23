import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  try {
    const response = await backendFetch(
      `/rfps/${encodeURIComponent(id)}/packet-files`
    );
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to list packet files";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  try {
    const contentType = request.headers.get("content-type") ?? "";
    // Forward raw multipart bytes — re-wrapping FormData breaks undici and
    // drops fields like citeTarget (same pattern as /api/rfps POST).
    const body = Buffer.from(await request.arrayBuffer());
    const response = await backendFetch(
      `/rfps/${encodeURIComponent(id)}/packet-files`,
      {
        method: "POST",
        body,
        headers: {
          "Content-Type": contentType || "multipart/form-data",
        },
        timeoutMs: 0,
      }
    );
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to upload packet file";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
