import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL =
  process.env.SUPABASE_URL || "https://xcxuashfybmvkcegmcur.supabase.co";

let _client: SupabaseClient | null = null;

/** Lazy client — never construct at module load (breaks `next build` without secrets). */
export function getSupabase(): SupabaseClient {
  if (_client) return _client;
  const key = (process.env.SUPABASE_SERVICE_ROLE_KEY || "").trim();
  if (!key) {
    throw new Error("SUPABASE_SERVICE_ROLE_KEY is not configured");
  }
  _client = createClient(SUPABASE_URL, key, {
    auth: { persistSession: false },
  });
  return _client;
}

/** Back-compat: existing `supabase.from(...)` call sites. */
export const supabase: SupabaseClient = new Proxy({} as SupabaseClient, {
  get(_target, prop) {
    const client = getSupabase();
    const value = (client as unknown as Record<string | symbol, unknown>)[prop];
    return typeof value === "function"
      ? (value as (...args: unknown[]) => unknown).bind(client)
      : value;
  },
});
