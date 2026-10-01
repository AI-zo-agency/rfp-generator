import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

async function forward(id: string, init: RequestInit) {
  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${encodeURIComponent(id)}/proposal/voice-rev`,
      { ...init, cache: "no-store" }
    );
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(id, { headers: { Accept: "application/json" } });
}

export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.json();
  return forward(id, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
}
