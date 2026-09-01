import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Every proposal-pipeline job currently queued or running, across all RFPs. */
export async function GET(request: NextRequest) {
  try {
    const includeGoNoGo = request.nextUrl.searchParams.get("includeGoNoGo");
    const query = includeGoNoGo ? `?includeGoNoGo=${includeGoNoGo}` : "";
    const response = await fetch(`${BACKEND_URL}/api/v1/proposals/jobs/active${query}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
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
      const snippet = text.replace(/\s+/g, " ").trim().slice(0, 240);
      return NextResponse.json(
        {
          detail: `Invalid JSON from backend (HTTP ${response.status}): ${snippet}`,
        },
        { status: 502 }
      );
    }

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      {
        detail: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 503 }
    );
  }
}
