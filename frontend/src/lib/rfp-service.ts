import { cache } from "react";
import { backendJson } from "@/lib/backend-api";
import { withDashboardPdfUrl } from "@/lib/rfp-pdf";
import { computeStats, mockRfps } from "@/lib/mock-rfps";
import type { DashboardStats, RfpRecord } from "@/types/rfp";

interface DashboardResponse {
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
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

export const getDashboardData = cache(async (): Promise<{
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
}> => {
  const { data, error } = await backendJson<DashboardResponse>("/rfps/dashboard");
  if (data) {
    return {
      rfps: data.rfps.map(withDashboardPdfUrl),
      allRfps: data.allRfps.map(withDashboardPdfUrl),
      stats: data.stats,
    };
  }

  if (process.env.NODE_ENV === "development" && process.env.USE_MOCK_RFPS === "true") {
    const allRfps = mockRfps;
    const rfps = allRfps.filter(
      (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
    );
    return { rfps, allRfps, stats: computeStats(allRfps) };
  }

  if (error) {
    console.warn("[rfp-service] dashboard unavailable:", error);
  }

  return {
    rfps: [],
    allRfps: [],
    stats: computeStats([]),
  };
});
