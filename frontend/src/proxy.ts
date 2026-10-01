/**
 * Every /api/* route on this site needs a signed-in user.
 *
 * Most of these routes forward to FastAPI, which checks the token again, but a
 * few read Supabase directly with the service-role key (src/lib/supabase-direct.ts)
 * and would otherwise be open to anyone.
 */
import { NextResponse, type NextRequest } from "next/server";
import { getSupabase } from "@/lib/supabase-direct";

// ponytail: per-process cache, one Supabase round trip per token per 5 minutes.
// A revoked token keeps working here for up to TTL_MS; FastAPI caches the same way.
const TTL_MS = 5 * 60_000;
const verified = new Map<string, number>();

async function isValid(token: string): Promise<boolean> {
  const now = Date.now();
  if ((verified.get(token) ?? 0) > now) return true;
  const { data, error } = await getSupabase().auth.getUser(token);
  if (error || !data.user) return false;
  if (verified.size > 5000) verified.clear();
  verified.set(token, now + TTL_MS);
  return true;
}

export async function proxy(request: NextRequest) {
  const header = request.headers.get("authorization") ?? "";
  const token = header.toLowerCase().startsWith("bearer ")
    ? header.slice(7).trim()
    : request.cookies.get("zo_token")?.value;
  if (token && (await isValid(token).catch(() => false))) {
    return NextResponse.next();
  }
  return NextResponse.json(
    { detail: "Sign in required" },
    { status: 401, headers: { "WWW-Authenticate": "Bearer" } },
  );
}

export const config = {
  matcher: "/api/:path*",
};
