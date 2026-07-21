import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "@/lib/proposal-stage-timeout";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const maxDuration = 900;
export const runtime = "nodejs";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string; sectionId: string }> }
) {
  const { id, sectionId } = await params;
  let body: {
    message?: string;
    selectionStart?: number;
    selectionEnd?: number;
    selectionText?: string;
    conversationHistory?: { role: string; content: string }[];
    proposalWide?: boolean;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  if (!body.message?.trim()) {
    return NextResponse.json({ detail: "message is required" }, { status: 400 });
  }

  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal/sections/${sectionId}/improve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: body.message,
          selectionStart: body.selectionStart,
          selectionEnd: body.selectionEnd,
          selectionText: body.selectionText,
          conversationHistory: body.conversationHistory,
          proposalWide: body.proposalWide === true,
        }),
        timeoutMs: PROPOSAL_STAGE_TIMEOUT_MS,
      }
    );
    const text = await res.text();
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
      return NextResponse.json(
        { detail: "Invalid JSON from backend." },
        { status: 502 }
      );
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Section improve failed";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
