import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "@/lib/proposal-stage-timeout";
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export const maxDuration = 3600;
export const runtime = "nodejs";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/generate/full`,
      { method: "POST", timeoutMs: PROPOSAL_STAGE_TIMEOUT_MS }
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
      error instanceof Error ? error.message : "Full proposal generation failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
