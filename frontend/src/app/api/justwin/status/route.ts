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
        return NextResponse.json({
          id: data.job.id,
          status: data.job.status,
          startedAt: data.job.started_at,
          finishedAt: data.job.finished_at,
          rfpsFound: data.job.rfps_found,
          pdfsDownloaded: data.job.pdfs_downloaded,
          error: data.job.error,
        });
      }
    }
  } catch {
    // If backend isn't reached, return idle
  }

  return NextResponse.json({ status: "idle" });
}
