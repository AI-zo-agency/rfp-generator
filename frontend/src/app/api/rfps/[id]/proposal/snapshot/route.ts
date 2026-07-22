import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

/** Preferred: GET /api/rfps/:id/proposal/snapshot?savedAt=ISO */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const savedAt = new URL(request.url).searchParams.get("savedAt")?.trim() || "";
  if (!savedAt) {
    return NextResponse.json({ detail: "savedAt is required" }, { status: 400 });
  }
  try {
    const backendUrl = new URL(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/snapshot`
    );
    backendUrl.searchParams.set("savedAt", savedAt);
    const response = await longRunningFetch(backendUrl.toString(), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    const data = await response.json();
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
