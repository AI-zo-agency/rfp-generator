import { longRunningFetch } from "@/lib/long-running-fetch";
import { readJson, relayJson, unreachable } from "@/lib/proxy-relay";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || "http://localhost:8001";

export async function PUT(request: Request) {
  const parsed = await readJson(request);
  if (parsed.response) return parsed.response;
  try {
    const res = await longRunningFetch(`${BACKEND_URL}/api/v1/brand-voice/active`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(parsed.body),
      cache: "no-store",
    });
    return await relayJson(res);
  } catch (error) {
    return unreachable(error);
  }
}
