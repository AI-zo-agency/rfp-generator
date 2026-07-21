import { supabase } from "@/lib/supabase-direct";
import {
  buildPipelineStatus,
  normalizeCheckpointForDisplay,
  type ProposalPipelineStatus,
} from "@/lib/proposal-pipeline-checkpoint";
import type { ProposalOutline, ProposalResearch } from "@/types/proposal";
import { apiDraftToOutline, type ApiProposalDraft } from "@/lib/proposal-api";

const EXCERPT_API_MAX = 500;
const MAX_FULFILL_LOG_LINES = 40;

function slimDraftPayload(draft: ApiProposalDraft): ApiProposalDraft {
  const snapshots = (draft.snapshots ?? []).map((snap) => ({
    ...snap,
    sections: [],
    sectionCount: snap.sectionCount ?? snap.sections?.length ?? 0,
  }));
  let lastFulfillReport = draft.lastFulfillReport;
  const logs = lastFulfillReport?.logs;
  if (Array.isArray(logs) && logs.length > MAX_FULFILL_LOG_LINES) {
    lastFulfillReport = {
      ...lastFulfillReport,
      logs: logs.slice(-MAX_FULFILL_LOG_LINES),
    };
  }
  return { ...draft, snapshots, lastFulfillReport };
}

function slimResearchPayload(research: ProposalResearch): ProposalResearch {
  const corpus = (research.evidenceCorpus ?? []).map((item) => {
    const excerpt = item.excerpt ?? "";
    if (excerpt.length <= EXCERPT_API_MAX) return item;
    return { ...item, excerpt: `${excerpt.slice(0, EXCERPT_API_MAX)}…` };
  });
  const plan = research.proposalExecutionPlan;
  let slimPlan = plan;
  if (plan && typeof plan === "object") {
    const writing = (plan as { writing?: Record<string, unknown> }).writing;
    const sectionPlans = writing?.sectionPlans as
      | { plans?: unknown[]; confidence?: unknown }
      | undefined;
    slimPlan = {
      metadata: (plan as { metadata?: unknown }).metadata,
      validation: (plan as { validation?: unknown }).validation,
      proposalMemory: (plan as { proposalMemory?: unknown }).proposalMemory,
      writing: {
        proposalOutline: writing?.proposalOutline,
        sectionPlans: sectionPlans
          ? {
              plans: (sectionPlans.plans ?? []).map((p) => {
                const row = p as Record<string, unknown>;
                return {
                  sectionId: row.sectionId,
                  title: row.title,
                  purpose: row.purpose,
                  wordBudget: row.wordBudget,
                };
              }),
              confidence: sectionPlans.confidence,
            }
          : undefined,
        retrievalPlan: writing?.retrievalPlan,
      },
    } as ProposalResearch["proposalExecutionPlan"];
  }
  return { ...research, evidenceCorpus: corpus, proposalExecutionPlan: slimPlan };
}

export type ProposalBundleResponse = {
  draft: ApiProposalDraft | null;
  research: ProposalResearch | null;
  pipelineStatus: ProposalPipelineStatus;
};

/** Same shape as FastAPI GET /rfps/{id}/proposal — reads Supabase only (shared across users). */
export async function loadProposalBundleFromSupabase(
  rfpId: string
): Promise<ProposalBundleResponse> {
  const [draftRow, researchRow] = await Promise.all([
    supabase
      .from("proposal_drafts")
      .select("payload")
      .eq("rfp_id", rfpId)
      .limit(1)
      .maybeSingle(),
    supabase
      .from("proposal_research")
      .select("payload")
      .eq("rfp_id", rfpId)
      .limit(1)
      .maybeSingle(),
  ]);

  if (draftRow.error) {
    throw new Error(draftRow.error.message);
  }
  if (researchRow.error) {
    throw new Error(researchRow.error.message);
  }

  const rawDraft = draftRow.data?.payload as ApiProposalDraft | null | undefined;
  const rawResearch = researchRow.data?.payload as ProposalResearch | null | undefined;

  const slimDraft = rawDraft ? slimDraftPayload(rawDraft) : null;
  const slimResearch = rawResearch ? slimResearchPayload(rawResearch) : null;

  const outline: ProposalOutline | null = slimDraft
    ? apiDraftToOutline(slimDraft)
    : null;
  const normalizedResearch = normalizeCheckpointForDisplay(
    outline,
    slimResearch ?? null
  );

  const pipelineStatus = buildPipelineStatus(outline, normalizedResearch);

  return {
    draft: slimDraft,
    research: normalizedResearch,
    pipelineStatus,
  };
}
