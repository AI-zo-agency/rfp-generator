import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { PROPOSAL_STAGE_MAX_DURATION_SEC } from "@/lib/proposal-stage-timeout";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const runtime = "nodejs";
export const maxDuration = PROPOSAL_STAGE_MAX_DURATION_SEC;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  let body: { useLlm?: boolean } = {};
  try {
    const text = await request.text();
    if (text.trim()) {
      body = JSON.parse(text) as { useLlm?: boolean };
    }
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const controller = new AbortController();
  const onClientAbort = () => controller.abort();
  request.signal.addEventListener("abort", onClientAbort);

  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/phase-4-auto-fix`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ useLlm: body.useLlm ?? true }),
        signal: controller.signal,
        timeoutMs: 0,
      }
    );
    const text = await res.text();
    if (!text.trim()) {
      return NextResponse.json(
        {
          detail:
            "Empty response from backend (auto-fix may have failed mid-request).",
        },
        { status: 502 }
      );
    }
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      return NextResponse.json(
        { detail: "Invalid JSON from backend." },
        { status: 502 }
      );
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return NextResponse.json({ detail: "Auto-fix stopped." }, { status: 499 });
    }
    const message =
      error instanceof Error ? error.message : "Pre-submit auto-fix failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  } finally {
    request.signal.removeEventListener("abort", onClientAbort);
  }
}
