import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
import { PROPOSAL_STAGE_MAX_DURATION_SEC } from "@/lib/proposal-stage-timeout";

export const runtime = "nodejs";
export const maxDuration = PROPOSAL_STAGE_MAX_DURATION_SEC;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  let useLlm = true;
  try {
    const text = await request.text();
    if (text.trim()) {
      const parsed = JSON.parse(text) as { useLlm?: boolean };
      useLlm = parsed.useLlm ?? true;
    }
  } catch {
    useLlm = true;
  }
  return proxyProposalPhasePost(
    id,
    "/proposal/fulfill-rfp-gaps",
    "Fulfill RFP gaps",
    {
      body: JSON.stringify({ useLlm }),
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
    }
  );
}
