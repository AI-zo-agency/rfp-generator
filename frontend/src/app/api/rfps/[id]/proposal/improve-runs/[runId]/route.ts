import { NextResponse } from "next/server";
import { authedFetch } from "@/lib/long-running-fetch";

export const runtime = "nodejs";
export const maxDuration = 60;

/** Status / result of an Ask Ralph improve run — polled after a page reload. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string; runId: string }> }
) {
  const { id, runId } = await params;
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.BACKEND_URL ||
    "http://localhost:8001";
  try {
    const res = await authedFetch(
      `${backendUrl}/api/v1/rfps/${id}/proposal/improve-runs/${encodeURIComponent(runId)}`,
      {
        headers: { Accept: "application/json" },
        cache: "no-store",
      }
    );
    const text = await res.text();
    let data: unknown = {};
    if (text.trim()) {
      try {
        data = JSON.parse(text);
      } catch {
        return NextResponse.json(
          { detail: "Invalid JSON from backend." },
          { status: 502 }
        );
      }
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Improve run fetch failed";
    return NextResponse.json(
      { detail: `Cannot reach API at ${backendUrl}. (${message})` },
      { status: 503 }
    );
  }
}
