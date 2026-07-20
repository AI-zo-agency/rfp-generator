import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.BACKEND_URL ||
    "http://localhost:8001";
  try {
    const response = await fetch(
      `${backendUrl}/api/v1/rfps/${id}/proposal/generation/clear-stop`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
        cache: "no-store",
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
      error instanceof Error ? error.message : "Clear stop flag failed";
    return NextResponse.json(
      { detail: `Cannot reach API at ${backendUrl}. (${message})` },
      { status: 503 }
    );
  }
}
