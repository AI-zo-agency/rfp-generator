/**
 * Signs every API request made from the browser.
 *
 * The app calls the FastAPI backend from ~80 places, some through the Next.js
 * /api routes and some directly. Rather than thread the token through each
 * call, this wraps window.fetch once, before the app starts: requests to
 * /api/* on this site or to the backend get `Authorization: Bearer <token>`.
 * A 401 triggers one token refresh and a retry; if that fails the user is sent
 * to /login.
 *
 * ponytail: a retried request re-sends `init.body`. Strings, FormData and Blobs
 * re-send fine; a one-shot ReadableStream body would not. None exist today.
 */
import { API_BASE_URL } from "@/lib/api/auth";
import { clearSession, freshAccessToken } from "@/lib/auth-session";

const originalFetch = window.fetch.bind(window);
const backendOrigin = new URL(API_BASE_URL, window.location.origin).origin;

function needsAuth(input: RequestInfo | URL): boolean {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(raw, window.location.origin);
  if (url.pathname.startsWith("/api/auth/")) return false; // login, signup, refresh
  if (url.origin === window.location.origin) return url.pathname.startsWith("/api/");
  return url.origin === backendOrigin;
}

function sendToLogin() {
  clearSession();
  const path = window.location.pathname;
  if (path !== "/login" && path !== "/signup") window.location.assign("/login");
}

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  if (!needsAuth(input)) return originalFetch(input, init);

  const send = (token: string | null) => {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
    return originalFetch(input, { ...init, headers });
  };

  const token = await freshAccessToken(originalFetch);
  const response = await send(token);
  if (response.status !== 401 || !token) return response;

  const renewed = await freshAccessToken(originalFetch, true);
  if (!renewed) {
    sendToLogin();
    return response;
  }
  const retried = await send(renewed);
  if (retried.status === 401) sendToLogin();
  return retried;
};
