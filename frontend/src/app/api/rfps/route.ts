import { backendFetch } from "@/lib/backend-api";
import { getDashboardData } from "@/lib/rfp-service";
import type { RfpPriority } from "@/types/rfp";
import { NextResponse } from "next/server";

export async function GET() {
  const data = await getDashboardData();

  return NextResponse.json({
    ...data,
    source: "backend",
  });
}

export async function POST(request: Request) {
  try {
    const contentType = request.headers.get("content-type") ?? "";
    const isMultipart = contentType.includes("multipart/form-data");

    const response = await backendFetch("/rfps", {
      method: "POST",
      body: isMultipart ? await request.formData() : await request.text(),
      headers: isMultipart ? undefined : { "Content-Type": contentType || "application/json" },
    });

    const text = await response.text();
    if (!text.trim()) {
      return NextResponse.json(
        { error: "Empty response from backend" },
        { status: 502 }
      );
    }

    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      return NextResponse.json(
        { error: "Invalid JSON from backend" },
        { status: 502 }
      );
    }

    if (!response.ok) {
      const detail =
        typeof data === "object" && data && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : typeof data === "object" && data && "error" in data
            ? String((data as { error: unknown }).error)
            : "Failed to create RFP";
      return NextResponse.json({ error: detail }, { status: response.status });
    }

    return NextResponse.json(
      { ok: true, rfp: data },
      { status: response.status }
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to create manual RFP.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
