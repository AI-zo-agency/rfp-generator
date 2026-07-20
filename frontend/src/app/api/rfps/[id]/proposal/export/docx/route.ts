import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "@/lib/proposal-stage-timeout";

export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/export/docx`,
      {
        method: "POST",
        timeoutMs: PROPOSAL_STAGE_TIMEOUT_MS,
        headers: { Accept: "application/octet-stream" },
      }
    );

    if (!res.ok) {
      const text = await res.text();
      let detail = "Word export failed";
      try {
        const parsed = JSON.parse(text) as { detail?: string };
        if (parsed.detail) detail = parsed.detail;
      } catch {
        if (text.trim()) detail = text.slice(0, 200);
      }
      return NextResponse.json({ detail }, { status: res.status });
    }

    const buffer = Buffer.from(await res.arrayBuffer());
    const disposition =
      res.headers.get("content-disposition") ??
      'attachment; filename="proposal.docx"';
    const contentType =
      res.headers.get("content-type") ??
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": disposition,
      },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Word export failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
