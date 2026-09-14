import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const maxDuration = 60;
export const runtime = "nodejs";

/** Start Go/No-Go in the background — returns immediately (status: running). */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const email = (request.headers.get("x-user-email") || "").trim().toLowerCase();

  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/rfps/${id}/analyze`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        ...(email.includes("@") ? { "X-User-Email": email.slice(0, 320) } : {}),
      },
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
