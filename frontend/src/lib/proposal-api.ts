import type {
  ProposalOutline,
  OutlineSection,
  ProposalResearch,
  ProposalBudget,
  PreSubmitReview,
  PreSubmitAutoFixReport,
} from "@/types/proposal";

interface ApiProposalSection {
  id: string;
  title: string;
  pageLimit?: number;
  wordTarget: number;
  required: boolean;
  custom: boolean;
  content: string;
  status: OutlineSection["status"];
  source: OutlineSection["source"];
  mode?: OutlineSection["mode"];
  designerNote?: string;
  kbRefs?: string[];
}

interface ApiProposalDraft {
  rfpId: string;
  sections: ApiProposalSection[];
  updatedAt: string;
  generatedAt?: string | null;
  provider?: string | null;
}

export function apiDraftToOutline(draft: ApiProposalDraft): ProposalOutline {
  return {
    updatedAt: draft.updatedAt,
    sections: draft.sections,
  };
}

export function outlineToApiDraft(
  rfpId: string,
  outline: ProposalOutline
): ApiProposalDraft {
  return {
    rfpId,
    updatedAt: outline.updatedAt,
    sections: outline.sections.map((s) => ({
      ...s,
      source:
        s.source === "custom"
          ? "generated"
          : (s.source as ApiProposalSection["source"]),
    })),
  };
}

/** Long timeout for staged proposal generation (browser → Next API route). */
const PROPOSAL_STAGE_TIMEOUT_MS = 25 * 60 * 1000;

function proposalPostInit(): RequestInit {
  return {
    method: "POST",
    signal: AbortSignal.timeout(PROPOSAL_STAGE_TIMEOUT_MS),
  };
}

export type FullProposalProgress =
  | "sections-1-3"
  | "phase-2"
  | "phase-3"
  | "phase-3-5-budget"
  | "recovering";

export function countSectionsWithContent(outline: ProposalOutline): number {
  return outline.sections.filter((s) => s.content?.trim()).length;
}

/** Load draft from DB when HTTP failed but backend may have finished saving. */
export async function recoverProposalDraftIfSaved(
  rfpId: string,
  options?: { minSectionsWithContent?: number }
): Promise<{ draft: ProposalOutline; research: ProposalResearch | null } | null> {
  const min = options?.minSectionsWithContent ?? 3;
  const { draft, research } = await fetchProposalDraft(rfpId);
  if (!draft || countSectionsWithContent(draft) < min) {
    return null;
  }
  return { draft, research };
}

/**
 * Run the full pipeline as three shorter requests so no single HTTP call
 * must stay open for the entire multi-batch Phase 3 run.
 */
export async function generateFullProposalStaged(
  rfpId: string,
  onProgress?: (stage: FullProposalProgress) => void
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  onProgress?.("sections-1-3");
  await generateProposalSections1to3(rfpId);

  onProgress?.("phase-2");
  await runPhase2Retrieval(rfpId);

  onProgress?.("phase-3");
  const { draft: drafted, research: afterPhase3 } = await runPhase3Drafting(rfpId);

  onProgress?.("phase-3-5-budget");
  const { budget, research: afterBudget, draft } = await runPhase3_5Budget(rfpId);
  return { draft: draft ?? drafted, research: afterBudget };
}

export async function fetchProposalDraft(rfpId: string): Promise<{
  draft: ProposalOutline | null;
  research: ProposalResearch | null;
  provider?: string | null;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal`, { cache: "no-store" });
  if (!res.ok) return { draft: null, research: null };
  const data = (await res.json()) as {
    draft: ApiProposalDraft | null;
    research: ProposalResearch | null;
  };
  if (!data.draft?.sections?.length) {
    return { draft: null, research: data.research ?? null };
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
    provider: data.draft.provider,
  };
}

export async function saveProposalDraft(
  rfpId: string,
  outline: ProposalOutline
): Promise<void> {
  await fetch(`/api/rfps/${rfpId}/proposal`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(outlineToApiDraft(rfpId, outline)),
  });
}

export async function generateFullProposalWithResearch(
  rfpId: string
): Promise<{ draft: ProposalOutline; research: ProposalResearch | null }> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/generate/full`,
    proposalPostInit()
  );
  const text = await res.text();
  let data: {
    detail?: string;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error(
      "Invalid response from server (full proposal may have timed out)."
    );
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Full proposal generation failed");
  }
  if (!data.draft) {
    throw new Error("No draft returned from server");
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
  };
}

export async function generateFullProposal(rfpId: string): Promise<ProposalOutline> {
  const { draft } = await generateFullProposalWithResearch(rfpId);
  return draft;
}

export async function generateProposalDraft(rfpId: string): Promise<ProposalOutline> {
  return generateFullProposal(rfpId);
}

export async function generateProposalSections1to3(
  rfpId: string
): Promise<ProposalOutline> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/generate/sections-1-3`,
    proposalPostInit()
  );
  const text = await res.text();
  let data: { detail?: string; draft?: ApiProposalDraft };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (generation may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Sections 1–3 generation failed");
  }
  if (!data.draft) {
    throw new Error("No draft returned from server");
  }
  return apiDraftToOutline(data.draft);
}

export async function runPhase2Retrieval(
  rfpId: string
): Promise<ProposalResearch> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-2-retrieval`, {
    ...proposalPostInit(),
  });
  const text = await res.text();
  let data: { detail?: string; research?: ProposalResearch };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (Phase 2 may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 2 retrieval failed");
  }
  if (!data.research) {
    throw new Error("No research data returned from server");
  }
  return data.research;
}

export async function runPhase3Drafting(
  rfpId: string
): Promise<{ draft: ProposalOutline; research: ProposalResearch }> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-3-drafting`, {
    ...proposalPostInit(),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (Phase 3 may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 3 drafting failed");
  }
  if (!data.draft || !data.research) {
    throw new Error("No draft or research returned from server");
  }
  return {
    draft: apiDraftToOutline(data.draft),
    research: data.research,
  };
}

export async function runPhase3_5Budget(
  rfpId: string
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-3-5-budget`, {
    ...proposalPostInit(),
  });
  const text = await res.text();
  let data: {
    detail?: string;
    budget?: ProposalBudget;
    research?: ProposalResearch;
    draft?: Parameters<typeof apiDraftToOutline>[0] | null;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (budget step may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Phase 3.5 budget failed");
  }
  if (!data.budget || !data.research) {
    throw new Error("No budget data returned from server");
  }
  return {
    budget: data.budget,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
  };
}

export async function generateProposalPricing(
  rfpId: string
): Promise<{
  budget: ProposalBudget;
  research: ProposalResearch;
  draft: ProposalOutline | null;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/pricing/generate`, {
    method: "POST",
  });
  const text = await res.text();
  let data: {
    detail?: string;
    budget?: ProposalBudget;
    research?: ProposalResearch;
    draft?: Parameters<typeof apiDraftToOutline>[0] | null;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (pricing may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pricing generation failed");
  }
  if (!data.budget || !data.research) {
    throw new Error("No budget data returned from server");
  }
  return {
    budget: data.budget,
    research: data.research,
    draft: data.draft ? apiDraftToOutline(data.draft) : null,
  };
}

export async function runPhase4PreSubmitReview(
  rfpId: string
): Promise<{ review: PreSubmitReview; research: ProposalResearch }> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-4-review`, {
    method: "POST",
  });
  const text = await res.text();
  let data: {
    detail?: string;
    review?: PreSubmitReview;
    research?: ProposalResearch;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from pre-submit review.");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pre-submit review failed");
  }
  if (!data.review || !data.research) {
    throw new Error("No review data returned");
  }
  return { review: data.review, research: data.research };
}

export async function runPhase4PreSubmitAutoFix(
  rfpId: string,
  options?: { useLlm?: boolean; signal?: AbortSignal }
): Promise<{
  review: PreSubmitReview;
  research: ProposalResearch;
  draft: ProposalOutline;
  autoFix: PreSubmitAutoFixReport;
}> {
  const res = await fetch(`/api/rfps/${rfpId}/proposal/phase-4-auto-fix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ useLlm: options?.useLlm ?? true }),
    signal: options?.signal,
  });
  const text = await res.text();
  let data: {
    detail?: string;
    review?: PreSubmitReview;
    research?: ProposalResearch;
    draft?: ApiProposalDraft;
    autoFix?: PreSubmitAutoFixReport;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from auto-fix.");
  }
  if (res.status === 499) {
    const err = new Error(data.detail ?? "Auto-fix stopped.");
    err.name = "AbortError";
    throw err;
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Pre-submit auto-fix failed");
  }
  if (!data.review || !data.research || !data.draft || !data.autoFix) {
    throw new Error("Incomplete auto-fix response");
  }
  return {
    review: data.review,
    research: data.research,
    draft: apiDraftToOutline(data.draft),
    autoFix: data.autoFix,
  };
}

export async function improveProposalSection(
  rfpId: string,
  sectionId: string,
  message: string
): Promise<{
  section: OutlineSection;
  draft: ProposalOutline;
  research: ProposalResearch | null;
  assistantMessage: string;
}> {
  const res = await fetch(
    `/api/rfps/${rfpId}/proposal/sections/${sectionId}/improve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }
  );
  const text = await res.text();
  let data: {
    detail?: string;
    section?: OutlineSection;
    draft?: ApiProposalDraft;
    research?: ProposalResearch;
    assistantMessage?: string;
  };
  try {
    data = text.trim() ? JSON.parse(text) : {};
  } catch {
    throw new Error("Invalid response from server (section improve may have timed out).");
  }
  if (!res.ok) {
    throw new Error(data.detail ?? "Section improve failed");
  }
  if (!data.section || !data.draft) {
    throw new Error("No section data returned from server");
  }
  return {
    section: data.section,
    draft: apiDraftToOutline(data.draft),
    research: data.research ?? null,
    assistantMessage:
      data.assistantMessage ??
      `Updated ${data.section.title}. Review the draft above.`,
  };
}
