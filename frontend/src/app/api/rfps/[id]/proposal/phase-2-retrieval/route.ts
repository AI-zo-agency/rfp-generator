import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
export const runtime = "nodejs";
export const maxDuration = 3600;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const bodyText = await request.text();
  return proxyProposalPhasePost(
    id,
    "/proposal/phase-2-retrieval",
    "Phase 2 retrieval",
    {
      body: bodyText.trim() ? bodyText : undefined,
      headers: bodyText.trim()
        ? { "Content-Type": "application/json", Accept: "application/json" }
        : { Accept: "application/json" },
    }
  );
}
