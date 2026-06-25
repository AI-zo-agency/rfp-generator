/**
 * Spawns the Playwright JustWin sync CLI.
 *
 * DISABLED: set JUSTWIN_SYNC_ENABLED=true in justwin-config.ts to re-enable.
 */
import { spawn } from "child_process";
import { randomUUID } from "crypto";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";
import {
  createSyncJob,
  finishSyncJob,
  getLatestSyncJob,
  getRunningSyncJob,
} from "@/lib/sync-jobs-api";

function tryParseJson(output: string): {
  rfpsFound?: number;
  pdfsDownloaded?: number;
  error?: string;
} | null {
  const lines = output
    .trim()
    .split("\n")
    .filter(Boolean)
    .reverse();

  for (const line of lines) {
    try {
      return JSON.parse(line) as {
        rfpsFound?: number;
        pdfsDownloaded?: number;
        error?: string;
      };
    } catch {
      continue;
    }
  }

  return null;
}

export async function startJustWinSync():
  Promise<{ jobId: string; status: "running" } | { error: string }> {
  if (!JUSTWIN_SYNC_ENABLED) {
    return { error: JUSTWIN_SYNC_DISABLED_MESSAGE };
  }

  if (await getRunningSyncJob()) {
    return { error: "Sync already in progress" };
  }

  const jobId = randomUUID();
  await createSyncJob(jobId);

  finishSyncJob(jobId, {
    status: "failed",
    rfpsFound: 0,
    pdfsDownloaded: 0,
    error: JUSTWIN_SYNC_DISABLED_MESSAGE,
  });

  return { error: JUSTWIN_SYNC_DISABLED_MESSAGE };
}

export { getLatestSyncJob, getRunningSyncJob };
