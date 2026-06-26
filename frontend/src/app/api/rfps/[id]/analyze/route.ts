import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";
/** Go/No-Go runs KB search + large JSON LLM (~60–90s). */
const ANALYZE_TIMEOUT_MS = 4 * 60 * 1000;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/rfps/${id}/analyze`, {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(ANALYZE_TIMEOUT_MS),
    });

    const text = await response.text();
    if (!text.trim()) {
      return NextResponse.json(
        { detail: "Empty response from backend." },
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

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    const isTimeout = message.toLowerCase().includes("timeout");
    return NextResponse.json(
      {
        detail: isTimeout
          ? "Go/No-Go analysis timed out — try again (large RFPs can take 1–2 minutes)."
          : `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: isTimeout ? 504 : 503 }
    );
  }
}
