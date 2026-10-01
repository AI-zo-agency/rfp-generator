/**
 * Browser-side session: the Supabase tokens from /api/auth/login.
 *
 * The access token lives in localStorage (read by the fetch hook in
 * src/instrumentation-client.ts) and in the `zo_token` cookie, so plain page
 * loads and links (PDFs, the Lead Finder page) reach the Next.js server with it.
 * Access tokens last about an hour; `freshAccessToken` renews them shortly
 * before they expire.
 */
import { API_BASE_URL } from "@/lib/api/auth";

export const TOKEN_COOKIE = "zo_token";
const REFRESH_EARLY_S = 120;

type Session = { access_token: string; refresh_token?: string; expires_at?: number };

function writeCookie(value: string, maxAgeS: number) {
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${TOKEN_COOKIE}=${value}; Path=/; Max-Age=${maxAgeS}; SameSite=Lax${secure}`;
}

export function saveSession(session: Session, user?: unknown) {
  localStorage.setItem("auth_token", session.access_token);
  if (session.refresh_token) localStorage.setItem("auth_refresh_token", session.refresh_token);
  if (session.expires_at) localStorage.setItem("auth_expires_at", String(session.expires_at));
  if (user !== undefined) localStorage.setItem("auth_user", JSON.stringify(user));
  const lifetime = session.expires_at ? session.expires_at - Math.floor(Date.now() / 1000) : 3600;
  writeCookie(session.access_token, Math.max(lifetime, 60));
}

export function clearSession() {
  for (const key of ["auth_token", "auth_refresh_token", "auth_expires_at", "auth_user"]) {
    localStorage.removeItem(key);
  }
  writeCookie("", 0);
}

let refreshing: Promise<string | null> | null = null;

async function refresh(fetchImpl: typeof fetch): Promise<string | null> {
  const refreshToken = localStorage.getItem("auth_refresh_token");
  if (!refreshToken) return null;
  try {
    const res = await fetchImpl(`${API_BASE_URL}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    saveSession(data.session, data.user);
    return data.session.access_token as string;
  } catch {
    return null;
  }
}

/** The current access token, renewed first if it is about to expire (or `force`). */
export async function freshAccessToken(fetchImpl: typeof fetch, force = false): Promise<string | null> {
  const token = localStorage.getItem("auth_token");
  const expiresAt = Number(localStorage.getItem("auth_expires_at") || 0);
  const expiring = expiresAt > 0 && expiresAt - Date.now() / 1000 < REFRESH_EARLY_S;
  if (token && !force && !expiring) return token;
  // One refresh at a time: a page firing ten requests at once must not burn the refresh token ten times.
  refreshing ??= refresh(fetchImpl).finally(() => {
    refreshing = null;
  });
  return (await refreshing) ?? (force ? null : token);
}
