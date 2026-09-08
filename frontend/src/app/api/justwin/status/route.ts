import { NextResponse } from "next/server";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export const runtime = "nodejs";

function parseSyncMeta(error: unknown): {
  error: string | null;
  rfpsSkipped?: number;
  rfpsCreated?: number;
} {
  if (typeof error !== "string" || !error.startsWith("ZO_SYNC_META:")) {
    return { error: (error as string | null) ?? null };
  }
  try {
    const meta = JSON.parse(error.slice("ZO_SYNC_META:".length)) as {
      rfpsSkipped?: number;
      rfpsCreated?: number;
    };
    return {
      error: null,
      rfpsSkipped: meta.rfpsSkipped,
      rfpsCreated: meta.rfpsCreated,
    };
  } catch {
    return { error: error as string };
  }
}

export async function GET() {
  if (!JUSTWIN_SYNC_ENABLED) {
    return NextResponse.json({
      status: "disabled",
      message: JUSTWIN_SYNC_DISABLED_MESSAGE || "JustWin sync is disabled",
    });
  }

  try {
    // Prefer an in-flight job so the global widget / modal stay accurate even
    // if "latest" briefly lags after enqueue.
    const [runningRes, latestRes] = await Promise.all([
      fetch(`${BACKEND_URL}/api/v1/sync-jobs/running`, { cache: "no-store" }),
      fetch(`${BACKEND_URL}/api/v1/sync-jobs/latest`, { cache: "no-store" }),
    ]);
    const runningData = runningRes.ok
      ? ((await runningRes.json()) as { job?: Record<string, unknown> })
      : { job: undefined };
    const latestData = latestRes.ok
      ? ((await latestRes.json()) as { job?: Record<string, unknown> })
      : { job: undefined };
    const job = runningData.job ?? latestData.job;
    if (job) {
      const meta = parseSyncMeta(job.error);
      const rfpsSkipped =
        typeof job.rfps_skipped === "number" ? job.rfps_skipped : meta.rfpsSkipped;
      const rfpsCreated =
        typeof job.rfps_created === "number" ? job.rfps_created : meta.rfpsCreated;
      return NextResponse.json({
        id: job.id,
        status: job.status,
        startedAt: job.started_at,
        finishedAt: job.finished_at,
        rfpsFound: job.rfps_found,
        rfpsCreated,
        rfpsSkipped,
        pdfsDownloaded: job.pdfs_downloaded,
        error: meta.error,
      });
    }
  } catch {
    // If backend isn't reached, return idle
  }

  return NextResponse.json({ status: "idle" });
}
