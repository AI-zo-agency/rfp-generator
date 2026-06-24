import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";
const GENERATE_TIMEOUT_MS = 25 * 60 * 1000;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  try {
    const response = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/generate`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: AbortSignal.timeout(GENERATE_TIMEOUT_MS),
      }
    );
    const text = await response.text();
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
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      { detail: `Cannot reach API at ${BACKEND_URL}. (${message})` },
      { status: 503 }
    );
  }
}
