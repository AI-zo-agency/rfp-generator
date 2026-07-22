import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export const runtime = "nodejs";

async function proxyPdf(
  request: Request,
  id: string,
  method: "GET" | "HEAD"
) {
  try {
    const response = await longRunningFetch(`${BACKEND_URL}/api/v1/rfps/${id}/pdf`, {
      method,
      cache: "no-store",
      redirect: "manual",
    });

    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (location) {
        return NextResponse.redirect(location, response.status);
      }
    }

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const message =
        typeof body === "object" && body && "detail" in body
          ? String((body as { detail: unknown }).detail)
          : "PDF not found";
      return NextResponse.json({ error: message }, { status: response.status });
    }

    if (method === "HEAD") {
      return new NextResponse(null, {
        status: response.status,
        headers: {
          "Content-Type": response.headers.get("content-type") ?? "application/pdf",
          "Content-Disposition": "inline",
          ...(response.headers.get("content-length")
            ? { "Content-Length": response.headers.get("content-length")! }
            : {}),
        },
      });
    }

    const buffer = await response.arrayBuffer();
    return new NextResponse(buffer, {
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": "inline",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to load PDF";
    return NextResponse.json(
      {
        error: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 502 }
    );
  }
}

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  return proxyPdf(request, id, "GET");
}

export async function HEAD(
  request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  return proxyPdf(request, id, "HEAD");
}
