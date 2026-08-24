import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

async function proxy(path: string, init?: RequestInit) {
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      { error: `Cannot reach API at ${BACKEND_URL}. (${message})` },
      { status: 503 }
    );
  }
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.text();
  return proxy(`/knowledge-base/corrections/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const documentId = new URL(request.url).searchParams.get("documentId") ?? "";
  return proxy(
    `/knowledge-base/corrections/${encodeURIComponent(id)}?document_id=${encodeURIComponent(documentId)}`,
    { method: "DELETE" }
  );
}
