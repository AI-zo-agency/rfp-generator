import { cache } from "react";
import { backendJson } from "@/lib/backend-api";
import { withDashboardPdfUrl } from "@/lib/rfp-pdf";
import { computeStats, mockActivity, mockRfps } from "@/lib/mock-rfps";
import type {
  ActivityItem,
  CurrentProposalItem,
  DashboardStats,
  RfpRecord,
} from "@/types/rfp";

interface DashboardResponse {
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
  recentActivity?: ActivityItem[];
  currentProposals?: CurrentProposalItem[];
  latestProposal?: CurrentProposalItem | null;
}

/**
 * Reads RFPs from FastAPI backend (Supabase Postgres or SQLite fallback on backend).
 */
export const getRfps = cache(async (): Promise<RfpRecord[]> => {
  const { data, error } = await backendJson<RfpRecord[]>("/rfps");
  if (data) return data.map(withDashboardPdfUrl);

  if (process.env.NODE_ENV === "development" && process.env.USE_MOCK_RFPS === "true") {
    return mockRfps;
  }

  if (error) {
    console.warn("[rfp-service] backend unavailable:", error);
  }
  return [];
});

export const getRfpById = cache(async (id: string): Promise<RfpRecord | null> => {
  const { data } = await backendJson<RfpRecord>(`/rfps/${encodeURIComponent(id)}`);
  if (data) return withDashboardPdfUrl(data);

  if (process.env.NODE_ENV === "development" && process.env.USE_MOCK_RFPS === "true") {
    return mockRfps.find((r) => r.id === id || r.externalId === id) ?? null;
  }
  return null;
});

function mockCurrentProposals(allRfps: RfpRecord[]): CurrentProposalItem[] {
  return allRfps
    .filter(
      (r) =>
        !["won", "lost", "passed", "submitted"].includes(r.status) &&
        /proposal|section|case stud|draft/i.test(r.lastActivityNote || "")
    )
    .sort(
      (a, b) =>
        new Date(b.lastActivity).getTime() - new Date(a.lastActivity).getTime()
    )
    .slice(0, 6)
    .map((r) => ({
      rfpId: r.id,
      rfpTitle: r.title,
      client: r.client,
      updatedAt: r.lastActivity,
      filledCount: 1,
      sectionCount: 1,
      stage: r.stage,
      lastActivityNote: r.lastActivityNote,
    }));
}

export const getDashboardData = cache(async (): Promise<{
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
  recentActivity: ActivityItem[];
  currentProposals: CurrentProposalItem[];
  latestProposal: CurrentProposalItem | null;
}> => {
  const { data, error } = await backendJson<DashboardResponse>("/rfps/dashboard");
  if (data) {
    const currentProposals = data.currentProposals ?? [];
    return {
      rfps: data.rfps.map(withDashboardPdfUrl),
      allRfps: data.allRfps.map(withDashboardPdfUrl),
      stats: data.stats,
      recentActivity: data.recentActivity ?? [],
      currentProposals,
      latestProposal: data.latestProposal ?? currentProposals[0] ?? null,
    };
  }

  if (process.env.NODE_ENV === "development" && process.env.USE_MOCK_RFPS === "true") {
    const allRfps = mockRfps;
    const rfps = allRfps.filter(
      (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
    );
    const currentProposals = mockCurrentProposals(allRfps);
    return {
      rfps,
      allRfps,
      stats: computeStats(allRfps),
      recentActivity: mockActivity,
      currentProposals,
      latestProposal: currentProposals[0] ?? null,
    };
  }

  if (error) {
    console.warn("[rfp-service] dashboard unavailable:", error);
  }

  return {
    rfps: [],
    allRfps: [],
    stats: computeStats([]),
    recentActivity: [],
    currentProposals: [],
    latestProposal: null,
  };
});
