import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";
const PRICING_TIMEOUT_MS = 6 * 60 * 1000;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/pricing/generate`,
      {
        method: "POST",
        signal: AbortSignal.timeout(PRICING_TIMEOUT_MS),
      }
    );
    const text = await res.text();
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
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Pricing generation failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
