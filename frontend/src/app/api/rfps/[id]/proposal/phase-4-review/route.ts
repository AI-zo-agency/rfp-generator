import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/phase-4-review`,
      { method: "POST", signal: AbortSignal.timeout(120_000) }
    );
    const text = await res.text();
    if (!text.trim()) {
      return NextResponse.json(
        { detail: "Empty response from backend." },
        { status: 502 }
      );
    }
    const data = JSON.parse(text) as unknown;
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Pre-submit review failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
