import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  try {
    const body = await request.json().catch(() => ({}));
    const response = await backendFetch(
      `/rfps/${encodeURIComponent(id)}/proposal/apply-packet-exhibit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Failed to apply packet exhibit";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
