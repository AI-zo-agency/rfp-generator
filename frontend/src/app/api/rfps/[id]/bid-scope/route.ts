import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export async function PUT(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;

  try {
    const body = await request.json();
    const response = await backendFetch(
      `/rfps/${encodeURIComponent(id)}/bid-scope`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to update bid scope";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
