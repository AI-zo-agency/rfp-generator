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
    const backendRes = await fetch(`${BACKEND_URL}/api/v1/sync-jobs/latest`);
    if (backendRes.ok) {
      const data = (await backendRes.json()) as { job?: Record<string, unknown> };
      if (data.job) {
        const meta = parseSyncMeta(data.job.error);
        const rfpsSkipped =
          typeof data.job.rfps_skipped === "number"
            ? data.job.rfps_skipped
            : meta.rfpsSkipped;
        const rfpsCreated =
          typeof data.job.rfps_created === "number"
            ? data.job.rfps_created
            : meta.rfpsCreated;
        return NextResponse.json({
          id: data.job.id,
          status: data.job.status,
          startedAt: data.job.started_at,
          finishedAt: data.job.finished_at,
          rfpsFound: data.job.rfps_found,
          rfpsCreated,
          rfpsSkipped,
          pdfsDownloaded: data.job.pdfs_downloaded,
          error: meta.error,
        });
      }
    }
  } catch {
    // If backend isn't reached, return idle
  }

  return NextResponse.json({ status: "idle" });
}
