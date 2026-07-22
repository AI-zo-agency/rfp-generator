import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
import { PROPOSAL_STAGE_MAX_DURATION_SEC } from "@/lib/proposal-stage-timeout";

export const runtime = "nodejs";
export const maxDuration = PROPOSAL_STAGE_MAX_DURATION_SEC;

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  return proxyProposalPhasePost(
    id,
    "/proposal/phase-4-finalize-gaps",
    "Finalize submission gaps"
  );
}
