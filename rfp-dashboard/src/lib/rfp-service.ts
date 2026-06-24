import { getAllRfps, getRfpById as getRfpByIdFromDb } from "@/lib/db";
import { computeStats, mockRfps } from "@/lib/mock-rfps";
import type { DashboardStats, RfpRecord } from "@/types/rfp";

/**
 * Reads synced JustWin RFPs from SQLite first, then optional API, then mock data.
 */
export async function getRfps(): Promise<RfpRecord[]> {
  try {
    const synced = getAllRfps();
    if (synced.length > 0) {
      return synced;
    }
  } catch {
    // Database not initialized yet.
  }

  const apiKey = process.env.JUSTWIN_API_KEY;
  const apiUrl = process.env.JUSTWIN_API_URL;

  if (apiKey && apiUrl) {
    try {
      const response = await fetch(`${apiUrl}/rfps`, {
        headers: {
          Authorization: `Bearer ${apiKey}`,
          Accept: "application/json",
        },
        next: { revalidate: 300 },
      });

      if (response.ok) {
        const data = (await response.json()) as RfpRecord[];
        return data;
      }
    } catch {
      // Fall through to mock data
    }
  }

  return mockRfps;
}

export async function getRfpById(id: string): Promise<RfpRecord | null> {
  try {
    const fromDb = getRfpByIdFromDb(id);
    if (fromDb) return fromDb;
  } catch {
    // Database not initialized yet.
  }

  const all = await getRfps();
  return all.find((r) => r.id === id || r.externalId === id) ?? null;
}

export async function getDashboardData(): Promise<{
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
}> {
  const allRfps = await getRfps();
  const rfps = allRfps.filter(
    (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return {
    rfps,
    allRfps,
    stats: computeStats(allRfps),
  };
}
