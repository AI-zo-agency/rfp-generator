import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
export const runtime = "nodejs";
export const maxDuration = 3600;

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
