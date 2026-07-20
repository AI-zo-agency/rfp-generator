import { proxyProposalPhasePost } from "@/lib/proposal-phase-route";

export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  let savedAt = "";
  try {
    const text = await request.text();
    if (text.trim()) {
      const parsed = JSON.parse(text) as { savedAt?: string };
      savedAt = parsed.savedAt ?? "";
    }
  } catch {
    savedAt = "";
  }
  if (!savedAt) {
    return new Response(JSON.stringify({ detail: "savedAt is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  return proxyProposalPhasePost(
    id,
    "/proposal/restore-snapshot",
    "Restore proposal snapshot",
    {
      body: JSON.stringify({ savedAt }),
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
    }
  );
}
