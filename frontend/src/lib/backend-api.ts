import { longRunningFetch, type LongRunningFetchInit } from "@/lib/long-running-fetch";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

export function backendUrl(path: string): string {
  const base = BACKEND_URL.replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}/api/v1${suffix}`;
}

/**
 * Next.js server → FastAPI. Uses undici with disabled idle timeouts so
 * production proxies do not abort while the backend is still working.
 */
export async function backendFetch(
  path: string,
  init?: LongRunningFetchInit
): Promise<Response> {
  return longRunningFetch(backendUrl(path), {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
}

export async function backendJson<T>(
  path: string,
  init?: LongRunningFetchInit
): Promise<{ data: T | null; status: number; error?: string }> {
  try {
    const response = await backendFetch(path, init);
    const text = await response.text();
    if (!text.trim()) {
      return {
        data: null,
        status: response.status,
        error: "Empty response from backend",
      };
    }
    const data = JSON.parse(text) as T;
    if (!response.ok) {
      const detail =
        typeof data === "object" && data && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : `Request failed (${response.status})`;
      return { data: null, status: response.status, error: detail };
    }
    return { data, status: response.status };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Backend unreachable";
    return { data: null, status: 503, error: message };
  }
}
