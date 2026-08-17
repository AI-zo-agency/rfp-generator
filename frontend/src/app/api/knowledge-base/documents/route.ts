import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

async function proxyJson(path: string, init?: RequestInit) {
  try {
    const response = await longRunningFetch(`${BACKEND_URL}/api/v1${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      {
        error: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 503 }
    );
  }
}

export async function GET() {
  return proxyJson("/knowledge-base/documents");
}

export async function POST(request: Request) {
  try {
    const contentType = request.headers.get("content-type") ?? "";
    // Forward raw multipart bytes — re-wrapping FormData breaks undici (422 missing fields).
    const body = Buffer.from(await request.arrayBuffer());
    const response = await longRunningFetch(`${BACKEND_URL}/api/v1/knowledge-base/documents`, {
      method: "POST",
      body,
      headers: {
        "Content-Type": contentType || "multipart/form-data",
      },
      cache: "no-store",
    });
    const text = await response.text();
    let data: unknown = {};
    if (text.trim()) {
      try {
        data = JSON.parse(text);
      } catch {
        return NextResponse.json(
          { error: "Invalid JSON from backend" },
          { status: 502 }
        );
      }
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      {
        error: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 503 }
    );
  }
}
