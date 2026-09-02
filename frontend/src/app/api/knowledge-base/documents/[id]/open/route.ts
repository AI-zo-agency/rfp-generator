import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const customId = new URL(request.url).searchParams.get("customId") ?? "";
  const query = customId
    ? `?custom_id=${encodeURIComponent(customId)}`
    : "";

  try {
    const response = await fetch(
      `${BACKEND_URL}/api/v1/knowledge-base/documents/${encodeURIComponent(id)}/open${query}`,
      { redirect: "manual", cache: "no-store" }
    );

    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (location) {
        return NextResponse.redirect(location, response.status);
      }
    }

    const text = await response.text();
    let detail = "Could not open this document.";
    if (text.trim()) {
      try {
        const body = JSON.parse(text) as { detail?: string };
        detail = body.detail ?? detail;
      } catch {
        detail = text;
      }
    }
    return NextResponse.json({ error: detail }, { status: response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      { error: `Cannot reach API at ${BACKEND_URL}. (${message})` },
      { status: 503 }
    );
  }
}
