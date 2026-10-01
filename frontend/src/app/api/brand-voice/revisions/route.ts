import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

function unreachable(error: unknown) {
  const message = error instanceof Error ? error.message : "Backend unreachable";
  return NextResponse.json({ error: message }, { status: 503 });
}

export async function GET() {
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/brand-voice/revisions`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error) {
    return unreachable(error);
  }
}

export async function POST(request: Request) {
  // Forward the raw multipart bytes. Re-wrapping FormData breaks undici.
  const contentType = request.headers.get("content-type") ?? "";
  const body = Buffer.from(await request.arrayBuffer());
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/brand-voice/revisions`, {
      method: "POST",
      body,
      headers: { "Content-Type": contentType },
      cache: "no-store",
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error) {
    return unreachable(error);
  }
}
