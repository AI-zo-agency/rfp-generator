import { loadProposalBundleFromSupabase } from "@/lib/proposal-supabase-read";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { PROPOSAL_STAGE_MAX_DURATION_SEC } from "@/lib/proposal-stage-timeout";
import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const maxDuration = PROPOSAL_STAGE_MAX_DURATION_SEC;
export const runtime = "nodejs";

/**
 * GET reads draft + research + pipeline checkpoint from Supabase — shared state for
 * every user and every reload; never blocked by Python generation workers.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const bundle = await loadProposalBundleFromSupabase(id);
    return NextResponse.json(bundle);
  } catch (err) {
    return NextResponse.json(
      { detail: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

/**
 * PUT proxies to the backend — no short abort; wait for FastAPI to finish the write.
 */
export async function PUT(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.text();

  try {
    const response = await longRunningFetch(
      `${BACKEND_URL}/api/v1/rfps/${id}/proposal`,
      {
        method: "PUT",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body,
        cache: "no-store",
        // 0 = no AbortSignal; undici idle timeouts disabled in longRunningFetch
        timeoutMs: 0,
      }
    );
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      {
        detail: `Cannot reach API at ${BACKEND_URL}. (${message})`,
      },
      { status: 503 }
    );
  }
}
