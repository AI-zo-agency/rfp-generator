import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;

  try {
    const response = await backendFetch(`/rfps/${encodeURIComponent(id)}/go`, {
      method: "POST",
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to update";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
