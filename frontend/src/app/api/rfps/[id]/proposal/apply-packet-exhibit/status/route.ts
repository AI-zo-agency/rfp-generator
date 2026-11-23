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
      `/rfps/${encodeURIComponent(id)}/proposal/apply-packet-exhibit/status`
    );
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Failed to check packet-exhibit apply status";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
