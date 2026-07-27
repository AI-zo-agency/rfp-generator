import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";
export const runtime = "nodejs";
export const maxDuration = 3600;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  let useLlm = true;
  let mode = "verify_scrub_only";
  try {
    const text = await request.text();
    if (text.trim()) {
      const parsed = JSON.parse(text) as { useLlm?: boolean; mode?: string };
      useLlm = parsed.useLlm ?? true;
      mode = parsed.mode ?? "verify_scrub_only";
    }
  } catch {
    useLlm = true;
    mode = "verify_scrub_only";
  }
  return proxyProposalPhasePost(
    id,
    "/proposal/fulfill-rfp-gaps",
    "Scan RFP VERIFY scrub",
    {
      body: JSON.stringify({ useLlm, mode }),
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
    }
  );
}
