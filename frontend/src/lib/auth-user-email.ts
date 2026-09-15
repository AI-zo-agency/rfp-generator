/**
 * Signed-in email from login (`auth_user` in localStorage).
 * Sent as X-User-Email so the backend can attribute proposal LLM spend.
 */
export function getAuthUserEmail(): string {
  if (typeof window === "undefined") return "";
  try {
    const raw = localStorage.getItem("auth_user");
    if (!raw) return "";
    const parsed = JSON.parse(raw) as { email?: unknown };
    const email =
      typeof parsed?.email === "string" ? parsed.email.trim().toLowerCase() : "";
    return email.includes("@") ? email.slice(0, 320) : "";
  } catch {
    return "";
  }
}

export function withAuthUserEmail(headers?: HeadersInit): HeadersInit {
  const email = getAuthUserEmail();
  const base = headers
    ? Object.fromEntries(new Headers(headers).entries())
    : {};
  if (!email) return base;
  return { ...base, "X-User-Email": email };
}
