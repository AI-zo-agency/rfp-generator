import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export async function POST() {
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/v1/knowledge-base/sync/google-drive`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
        cache: "no-store",
      }
    );
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      { detail: message },
      { status: 503 }
    );
  }
}
