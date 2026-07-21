import { loadProposalBundleFromSupabase } from "@/lib/proposal-supabase-read";
import { NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";
const PROXY_TIMEOUT_MS = 12_000;

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
 * PUT still proxies to the backend — writes need to go through the API.
 */
export async function PUT(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = await request.text();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/rfps/${id}/proposal`, {
      method: "PUT",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body,
      cache: "no-store",
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    const timedOut = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        detail: timedOut
          ? `API request timed out after ${PROXY_TIMEOUT_MS / 1000}s — backend may be busy generating.`
          : `Cannot reach API at ${BACKEND_URL}. (${message})`,
      },
      { status: 503 }
    );
  } finally {
    clearTimeout(timer);
  }
}
