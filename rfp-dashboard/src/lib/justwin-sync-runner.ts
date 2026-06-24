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
} from "@/lib/db";

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

export function startJustWinSync():
  | { jobId: string; status: "running" }
  | { error: string } {
  if (!JUSTWIN_SYNC_ENABLED) {
    return { error: JUSTWIN_SYNC_DISABLED_MESSAGE };
  }

  if (getRunningSyncJob()) {
    return { error: "Sync already in progress" };
  }

  const jobId = randomUUID();
  createSyncJob(jobId);

  // Playwright sync — uncomment when re-enabled:
  // const child = spawn("npm", ["run", "sync:justwin", "--", jobId], {
  //   cwd: process.cwd(),
  //   stdio: ["ignore", "pipe", "pipe"],
  //   env: {
  //     ...process.env,
  //     JUSTWIN_RFP_TITLE_FILTER:
  //       process.env.JUSTWIN_RFP_TITLE_FILTER ??
  //       "Advertising, Marketing, Communications for Tennessee Board of Regents",
  //   },
  //   shell: process.platform === "win32",
  // });
  //
  // let output = "";
  // child.stdout?.on("data", (chunk: Buffer) => {
  //   output += chunk.toString();
  // });
  // child.stderr?.on("data", (chunk: Buffer) => {
  //   output += chunk.toString();
  // });
  //
  // child.on("close", (code) => {
  //   const parsed = tryParseJson(output);
  //   finishSyncJob(jobId, {
  //     status: code === 0 ? "completed" : "failed",
  //     rfpsFound: parsed?.rfpsFound ?? 0,
  //     pdfsDownloaded: parsed?.pdfsDownloaded ?? 0,
  //     error:
  //       code !== 0
  //         ? (parsed?.error ?? (output.trim() || "Sync failed"))
  //         : undefined,
  //   });
  // });

  finishSyncJob(jobId, {
    status: "failed",
    rfpsFound: 0,
    pdfsDownloaded: 0,
    error: JUSTWIN_SYNC_DISABLED_MESSAGE,
  });

  return { error: JUSTWIN_SYNC_DISABLED_MESSAGE };
}

export { getLatestSyncJob, getRunningSyncJob };
