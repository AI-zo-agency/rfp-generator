import { NextResponse } from "next/server";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";

// import { startJustWinSync } from "@/lib/justwin-sync-runner";

export const runtime = "nodejs";

export async function POST() {
  if (!JUSTWIN_SYNC_ENABLED) {
    return NextResponse.json(
      { error: JUSTWIN_SYNC_DISABLED_MESSAGE },
      { status: 503 }
    );
  }

  // Playwright sync disabled — uncomment when JUSTWIN_SYNC_ENABLED is true:
  // const result = startJustWinSync();
  // if ("error" in result) {
  //   return NextResponse.json(result, { status: 409 });
  // }
  // return NextResponse.json(result);

  return NextResponse.json(
    { error: "JustWin sync runner is not wired" },
    { status: 503 }
  );
}
