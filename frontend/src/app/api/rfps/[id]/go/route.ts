import { NextResponse } from "next/server";
import { updateRfpGoNoGo } from "@/lib/db";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;

  try {
    const updated = updateRfpGoNoGo(id, "go");
    if (!updated) {
      return NextResponse.json({ error: "RFP not found" }, { status: 404 });
    }
    return NextResponse.json({ ok: true, goNoGo: "go" });
  } catch {
    return NextResponse.json({ error: "Failed to update" }, { status: 500 });
  }
}
