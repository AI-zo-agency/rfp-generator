import { backendFetch, backendJson } from "@/lib/backend-api";
import type { RfpRecord } from "@/types/rfp";

export interface SyncJobRow {
  id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  rfps_found: number;
  pdfs_downloaded: number;
  error: string | null;
}

export async function upsertRfpViaBackend(rfp: RfpRecord): Promise<void> {
  const response = await backendFetch("/rfps/upsert", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rfp),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Upsert failed (${response.status})`);
  }
}

export async function createSyncJob(id: string): Promise<void> {
  await backendJson("/sync-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

export async function finishSyncJob(
  id: string,
  result: {
    status: "completed" | "failed";
    rfpsFound: number;
    pdfsDownloaded: number;
    error?: string;
  }
): Promise<void> {
  await backendJson(`/sync-jobs/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status: result.status,
      rfpsFound: result.rfpsFound,
      pdfsDownloaded: result.pdfsDownloaded,
      error: result.error ?? null,
    }),
  });
}

export async function getLatestSyncJob(): Promise<SyncJobRow | null> {
  const { data } = await backendJson<{ job: SyncJobRow | null }>("/sync-jobs/latest");
  return data?.job ?? null;
}

export async function getRunningSyncJob(): Promise<SyncJobRow | null> {
  const { data } = await backendJson<{ job: SyncJobRow | null }>("/sync-jobs/running");
  return data?.job ?? null;
}
