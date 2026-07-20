import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";
const PROXY_TIMEOUT_MS = 75_000;

async function proxy(path: string, init?: RequestInit) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1${path}`, {
      ...init,
      signal: controller.signal,
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
    const timedOut = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        detail: timedOut
          ? `API request timed out after ${PROXY_TIMEOUT_MS / 1000}s — backend may be busy generating. Retry shortly.`
          : `Cannot reach API at ${BACKEND_URL}. (${message})`,
      },
      { status: 503 }
    );
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return proxy(`/rfps/${id}/proposal`);
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.text();
  return proxy(`/rfps/${id}/proposal`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body,
  });
}
