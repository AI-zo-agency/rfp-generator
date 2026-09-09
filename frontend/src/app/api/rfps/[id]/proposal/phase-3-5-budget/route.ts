import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
export const runtime = "nodejs";
export const maxDuration = 3600;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const raw = await request.text();
  return proxyProposalPhasePost(
    id,
    "/proposal/phase-3-5-budget",
    "Phase 3.5 budget",
    {
      body: raw.trim() ? raw : undefined,
      headers: raw.trim()
        ? { "Content-Type": "application/json", Accept: "application/json" }
        : { Accept: "application/json" },
    }
  );
}
