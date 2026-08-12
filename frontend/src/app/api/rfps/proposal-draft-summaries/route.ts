import { isSectionDrafted } from "@/lib/proposal-section-health";
import { supabase } from "@/lib/supabase-direct";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type DraftPayload = {
  sections?: Array<{ content?: string | null }>;
  updatedAt?: string;
  updated_at?: string;
};

/**
 * Lightweight draft progress for every RFP that has a proposal_drafts row.
 * Used by the Proposals Switch-RFP drawer filters (not started / in progress / done).
 * Reads Supabase directly so it stays available while the Python backend is busy.
 */
export async function GET() {
  try {
    const { data, error } = await supabase
      .from("proposal_drafts")
      .select("rfp_id,updated_at,payload")
      .order("updated_at", { ascending: false });

    if (error) {
      return NextResponse.json(
        { error: `Supabase error: ${error.message}` },
        { status: 502 }
      );
    }

    const summaries = (data || []).map((row) => {
      const payload = (row.payload || {}) as DraftPayload;
      const sections = Array.isArray(payload.sections) ? payload.sections : [];
      const filledCount = sections.filter((s) =>
        isSectionDrafted(s?.content)
      ).length;
      const updatedAt =
        (typeof row.updated_at === "string" && row.updated_at) ||
        payload.updatedAt ||
        payload.updated_at ||
        "";
      return {
        rfpId: String(row.rfp_id || ""),
        filledCount,
        sectionCount: sections.length,
        updatedAt: String(updatedAt),
      };
    }).filter((s) => s.rfpId);

    return NextResponse.json({ summaries });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
