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

export async function POST(request: Request) {
  if (!JUSTWIN_SYNC_ENABLED) {
    return NextResponse.json(
      { error: JUSTWIN_SYNC_DISABLED_MESSAGE || "JustWin sync is disabled" },
      { status: 503 }
    );
  }

  let body: { syncMode?: string; syncDate?: string; tab?: string } = {};
  try {
    body = (await request.json()) as typeof body;
  } catch {
    // optional body
  }

  const syncMode = body.syncMode || "today";
  const syncDate =
    body.syncDate || new Date().toISOString().slice(0, 10);
  const tab = body.tab || "all";

  try {
    const backendRes = await fetch(`${BACKEND_URL}/api/v1/sync-jobs/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ syncMode, syncDate, tab }),
    });

    const data = await backendRes.json().catch(() => ({}));

    if (!backendRes.ok) {
      // Surface the backend's reason (already running, bad date, ...) instead of
      // reporting a sync that never started.
      return NextResponse.json(
        { error: data?.detail || `Sync could not be started (${backendRes.status})` },
        { status: backendRes.status }
      );
    }

    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      {
        error:
          "Could not reach the backend to start the sync. Make sure the API is running on " +
          BACKEND_URL,
      },
      { status: 503 }
    );
  }
}
