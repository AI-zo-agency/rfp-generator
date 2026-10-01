import { NextResponse } from "next/server";
import { longRunningFetch } from "@/lib/long-running-fetch";
import { relayJson, unreachable } from "@/lib/proxy-relay";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";
const MAX_UPLOAD_BYTES = 1_000_000; // the file cap is 200 KB; this leaves room for the multipart envelope

export async function GET() {
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/brand-voice/revisions`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    return await relayJson(res);
  } catch (error) {
    return unreachable(error);
  }
}

export async function POST(request: Request) {
  const tooLarge = () => NextResponse.json({ error: "The file is larger than 200 KB." }, { status: 413 });
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
    return NextResponse.json({ error: "Upload the file as a form." }, { status: 400 });
  }
  if (Number(request.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES) return tooLarge();
  // Forward the raw multipart bytes. Re-wrapping FormData breaks undici.
  const body = Buffer.from(await request.arrayBuffer());
  if (body.length > MAX_UPLOAD_BYTES) return tooLarge(); // no content-length (chunked)
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/brand-voice/revisions`, {
      method: "POST",
      body,
      headers: { "Content-Type": contentType },
      cache: "no-store",
    });
    return await relayJson(res);
  } catch (error) {
    return unreachable(error);
  }
}
