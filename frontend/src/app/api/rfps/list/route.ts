import { supabase } from "@/lib/supabase-direct";
import { withDashboardPdfUrl } from "@/lib/rfp-pdf";
import { mapSupabaseRfpRow } from "@/lib/supabase-rfp-map";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Reads RFP list directly from Supabase — completely independent of the
 * Python backend, so the dashboard never blocks during generation.
 */
export async function GET() {
  try {
    const { data, error } = await supabase
      .from("rfps")
      .select("*")
      .order("synced_at", { ascending: false })
      .order("received_date", { ascending: false });

    if (error) {
      return NextResponse.json(
        { error: `Supabase error: ${error.message}` },
        { status: 502 }
      );
    }

    const list = (data || []).map((row) =>
      withDashboardPdfUrl(mapSupabaseRfpRow(row as Record<string, unknown>))
    );
    return NextResponse.json(list);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
