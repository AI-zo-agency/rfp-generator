import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string; savedAt: string }> }
) {
  const { id, savedAt } = await params;
  const encoded = encodeURIComponent(savedAt);
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/snapshot/${encoded}`,
      { cache: "no-store", headers: { Accept: "application/json" } }
    );
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
