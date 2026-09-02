import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

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
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      { error: `Cannot reach API at ${BACKEND_URL}. (${message})` },
      { status: 503 }
    );
  }
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const customId = new URL(request.url).searchParams.get("customId") ?? "";
  const query = customId
    ? `?custom_id=${encodeURIComponent(customId)}`
    : "";
  return proxy(`/knowledge-base/documents/${encodeURIComponent(id)}${query}`, {
    method: "DELETE",
  });
}
