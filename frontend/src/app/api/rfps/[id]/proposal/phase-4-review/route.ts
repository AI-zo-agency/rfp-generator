import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";

export const runtime = "nodejs";
export const maxDuration = 900;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return proxyProposalPhasePost(
    id,
    "/proposal/phase-4-review",
    "Pre-submit review"
  );
}
