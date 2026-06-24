import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";

async function proxy(path: string, init?: RequestInit) {
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1${path}`, {
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
        detail: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 503 }
    );
  }
}

export async function GET() {
  return proxy("/knowledge-base/folders");
}
