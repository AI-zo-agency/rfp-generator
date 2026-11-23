import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";

export const runtime = "nodejs";
export const maxDuration = 3600;

/**
 * Sections 1–3 start. Kept as a sibling of phase-2-retrieval (not nested under
 * generate/) so Next App Router reliably matches the POST.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const incoming = new URL(request.url);
  const qs = incoming.searchParams.toString();
  const suffix = qs
    ? `/proposal/generate/sections-1-3?${qs}`
    : "/proposal/generate/sections-1-3";
  const bodyText = await request.text();
  return proxyProposalPhasePost(id, suffix, "Sections 1–3 generation", {
    body: bodyText.trim() ? bodyText : undefined,
    headers: bodyText.trim()
      ? { "Content-Type": "application/json", Accept: "application/json" }
      : { Accept: "application/json" },
  });
}
