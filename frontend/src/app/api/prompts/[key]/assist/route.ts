import { longRunningFetch } from "@/lib/long-running-fetch";
import { relayJson, unreachable } from "@/lib/proxy-relay";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export async function POST(request: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/prompts/${encodeURIComponent(key)}/assist`, {
      method: "POST",
      body: await request.text(),
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      cache: "no-store",
    });
    return await relayJson(res);
  } catch (error) {
    return unreachable(error);
  }
}
