import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import {
  PROPOSAL_STAGE_TIMEOUT_MS,
} from "@/lib/proposal-stage-timeout";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

/** Re-export for callers that want the literal ceiling (seconds). */
export const proposalStageMaxDuration = 3600;

export async function proxyProposalPhasePost(
  rfpId: string,
  pathSuffix: string,
  errorLabel: string,
  options?: { timeoutMs?: number; body?: string; headers?: HeadersInit }
): Promise<NextResponse> {
  const timeoutMs = options?.timeoutMs ?? PROPOSAL_STAGE_TIMEOUT_MS;
  try {
    const res = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${rfpId}${pathSuffix}`,
      {
        method: "POST",
        timeoutMs,
        body: options?.body,
        headers: options?.headers,
      }
    );
    const text = await res.text();
    if (!text.trim()) {
      return NextResponse.json(
        {
          detail: `Empty response from backend (${errorLabel} may have timed out).`,
        },
        { status: 502 }
      );
    }
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      const snippet = text.replace(/\s+/g, " ").slice(0, 180);
      return NextResponse.json(
        {
          detail:
            res.status >= 500
              ? `${errorLabel} failed on the server (HTTP ${res.status}). Try again in a moment.`
              : `Invalid JSON from backend (HTTP ${res.status}): ${snippet}`,
        },
        { status: 502 }
      );
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : `${errorLabel} failed`;
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
