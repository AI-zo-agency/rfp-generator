import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";
const SELF_EDIT_TIMEOUT_MS = 10 * 60 * 1000;

export const maxDuration = 900;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/phase-3-6-self-edit`,
      {
        method: "POST",
        signal: AbortSignal.timeout(SELF_EDIT_TIMEOUT_MS),
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
      error instanceof Error ? error.message : "Self-edit loop failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
