import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";
const PHASE3_TIMEOUT_MS = 25 * 60 * 1000;

export const maxDuration = 900;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/phase-3-drafting`,
      {
        method: "POST",
        signal: AbortSignal.timeout(PHASE3_TIMEOUT_MS),
      }
    );
    const text = await res.text();
    if (!text.trim()) {
      return NextResponse.json(
        { detail: "Empty response from backend (request may have timed out)." },
        { status: 502 }
      );
    }
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      return NextResponse.json(
        { detail: "Invalid JSON from backend." },
        { status: 502 }
      );
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Phase 3 drafting failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
