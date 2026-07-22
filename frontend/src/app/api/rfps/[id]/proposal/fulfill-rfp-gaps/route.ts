import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
export const runtime = "nodejs";
export const maxDuration = 3600;

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
