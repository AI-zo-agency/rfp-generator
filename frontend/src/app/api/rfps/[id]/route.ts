import { supabase } from "@/lib/supabase-direct";
import { withDashboardPdfUrl } from "@/lib/rfp-pdf";
import { mapSupabaseRfpRow } from "@/lib/supabase-rfp-map";
import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";

export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

/**
 * GET reads single RFP directly from Supabase — independent of backend.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const { data, error } = await supabase
      .from("rfps")
      .select("*")
      .or(`id.eq.${id},external_id.eq.${id}`)
      .limit(1)
      .maybeSingle();

    if (error) {
      return NextResponse.json(
        { detail: `Supabase error: ${error.message}` },
        { status: 502 }
      );
    }
    if (!data) {
      return NextResponse.json({ detail: "RFP not found" }, { status: 404 });
    }

    return NextResponse.json(
      withDashboardPdfUrl(mapSupabaseRfpRow(data as Record<string, unknown>))
    );
  } catch (err) {
    return NextResponse.json(
      { detail: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  try {
    const response = await longRunningFetch(`${BACKEND_URL}/api/v1/rfps/${id}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Backend unreachable";
    return NextResponse.json(
      {
        detail: `Cannot reach API at ${BACKEND_URL}. Start the FastAPI backend. (${message})`,
      },
      { status: 503 }
    );
  }
}
