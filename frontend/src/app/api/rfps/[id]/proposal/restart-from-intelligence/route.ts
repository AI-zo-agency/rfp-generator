import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

export const runtime = "nodejs";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.BACKEND_URL ||
    "http://localhost:8001";
  const bodyText = await request.text();
  try {
    const response = await longRunningFetch(
      `${backendUrl}/api/v1/rfps/${id}/proposal/restart-from-intelligence`,
      {
        method: "POST",
        headers: bodyText.trim()
          ? { "Content-Type": "application/json", Accept: "application/json" }
          : { Accept: "application/json" },
        cache: "no-store",
        body: bodyText.trim() ? bodyText : undefined,
      }
    );
    const text = await response.text();
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
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Failed to restart from intelligence";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
