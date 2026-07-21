import { backendFetch } from "@/lib/backend-api";
import { withDashboardPdfUrl } from "@/lib/rfp-pdf";
import { computeStats } from "@/lib/mock-rfps";
import type { DashboardStats, RfpRecord } from "@/types/rfp";
import { NextResponse } from "next/server";

const DASHBOARD_PROXY_TIMEOUT_MS = 45_000;

export async function GET() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DASHBOARD_PROXY_TIMEOUT_MS);
  try {
    const response = await backendFetch("/rfps/dashboard", {
      signal: controller.signal,
    });
    const text = await response.text();
    if (!text.trim()) {
      return NextResponse.json(
        { error: "Empty response from backend", rfps: [], allRfps: [], stats: computeStats([]) },
        { status: 502 }
      );
    }
    const data = JSON.parse(text) as {
      rfps: RfpRecord[];
      allRfps: RfpRecord[];
      stats: DashboardStats;
    };
    if (!response.ok) {
      return NextResponse.json(
        { error: "Dashboard request failed", rfps: [], allRfps: [], stats: computeStats([]) },
        { status: response.status }
      );
    }
    return NextResponse.json({
      rfps: data.rfps.map(withDashboardPdfUrl),
      allRfps: data.allRfps.map(withDashboardPdfUrl),
      stats: data.stats,
      source: "backend",
    });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        error: timedOut
          ? "Dashboard timed out — backend may be busy. Retry shortly."
          : error instanceof Error
            ? error.message
            : "Backend unreachable",
        rfps: [],
        allRfps: [],
        stats: computeStats([]),
      },
      { status: timedOut ? 504 : 503 }
    );
  } finally {
    clearTimeout(timer);
  }
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
