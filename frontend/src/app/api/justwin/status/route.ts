import { NextResponse } from "next/server";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";

// import { getLatestSyncJob } from "@/lib/justwin-sync-runner";

export const runtime = "nodejs";

export async function GET() {
  if (!JUSTWIN_SYNC_ENABLED) {
    return NextResponse.json({
      status: "disabled",
      message: JUSTWIN_SYNC_DISABLED_MESSAGE,
    });
  }

  // const job = getLatestSyncJob();
  // if (!job) {
  //   return NextResponse.json({ status: "idle" });
  // }
  // return NextResponse.json({
  //   id: job.id,
  //   status: job.status,
  //   startedAt: job.started_at,
  //   finishedAt: job.finished_at,
  //   rfpsFound: job.rfps_found,
  //   pdfsDownloaded: job.pdfs_downloaded,
  //   error: job.error,
  // });

  return NextResponse.json({ status: "idle" });
}
