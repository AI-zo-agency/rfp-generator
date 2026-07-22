/**
 * Shared FE ↔ BE timeout policy for production.
 *
 * Short AbortControllers on Next.js API proxies were aborting healthy backend
 * work (proposal PUT at 12s, dashboard at 45s, undici headersTimeout at 5m).
 *
 * Default: do not artificially abort Next → backend calls. Undici timeouts are
 * disabled (0). Callers may still pass an explicit AbortSignal (user cancel).
 *
 * Override with env if a platform hard-cap is required:
 *   BACKEND_PROXY_TIMEOUT_MS / PROPOSAL_STAGE_TIMEOUT_MS / NEXT_PUBLIC_PROPOSAL_STAGE_TIMEOUT_MS
 */

function envMs(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

/** 0 = no abort timeout (wait until response or connection error). */
export const BACKEND_PROXY_TIMEOUT_MS = envMs("BACKEND_PROXY_TIMEOUT_MS", 0);

/**
 * Browser → Next stage posts. 0 = no AbortSignal.timeout.
 * Default 0 so generation / improve never die from a client timer.
 */
export const PROPOSAL_STAGE_TIMEOUT_MS = envMs(
  "NEXT_PUBLIC_PROPOSAL_STAGE_TIMEOUT_MS",
  envMs("PROPOSAL_STAGE_TIMEOUT_MS", 0)
);

/**
 * Next.js route segment config (seconds). Must be a numeric literal so Next
 * can statically analyze `export const maxDuration = …` in route files.
 * Platforms that honor maxDuration (e.g. Vercel) use this ceiling;
 * self-hosted Railway/Docker typically ignore it.
 */
export const PROPOSAL_STAGE_MAX_DURATION_SEC = 3600;

/** Soft ceiling only when a finite AbortSignal is required by a caller. */
export const PROPOSAL_STAGE_FALLBACK_TIMEOUT_MS = 60 * 60 * 1000;
