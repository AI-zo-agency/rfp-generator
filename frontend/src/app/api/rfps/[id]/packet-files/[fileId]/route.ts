import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string; fileId: string }> }
) {
  const { id, fileId } = await context.params;
  try {
    const response = await backendFetch(
      `/rfps/${encodeURIComponent(id)}/packet-files/${encodeURIComponent(fileId)}`,
      { method: "DELETE" }
    );
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to delete packet file";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
