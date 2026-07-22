/**
 * Server-side fetch for Next.js → FastAPI.
 * Node's native fetch uses undici with a 300s headersTimeout by default —
 * that aborts proxies while uvicorn is still working. This agent disables
 * undici idle timeouts unless BACKEND_PROXY_TIMEOUT_MS is set.
 */
import { Agent, fetch as undiciFetch, type RequestInit as UndiciRequestInit } from "undici";
import {
  BACKEND_PROXY_TIMEOUT_MS,
  PROPOSAL_STAGE_FALLBACK_TIMEOUT_MS,
  PROPOSAL_STAGE_TIMEOUT_MS,
} from "./proposal-stage-timeout";

/** 0 disables the timeout in undici. */
const UNDICI_IDLE_MS =
  BACKEND_PROXY_TIMEOUT_MS > 0
    ? BACKEND_PROXY_TIMEOUT_MS + 120_000
    : 0;

const DISPATCHER = new Agent({
  headersTimeout: UNDICI_IDLE_MS,
  bodyTimeout: UNDICI_IDLE_MS,
  connectTimeout: 120_000,
  keepAliveTimeout: 120_000,
  keepAliveMaxTimeout: 600_000,
});

export type LongRunningFetchInit = RequestInit & {
  /** 0 or omit = no AbortSignal timeout (wait for backend). */
  timeoutMs?: number;
};

function resolveTimeoutMs(explicit?: number): number {
  if (explicit != null) return explicit;
  if (BACKEND_PROXY_TIMEOUT_MS > 0) return BACKEND_PROXY_TIMEOUT_MS;
  if (PROPOSAL_STAGE_TIMEOUT_MS > 0) return PROPOSAL_STAGE_TIMEOUT_MS;
  return 0;
}

export async function longRunningFetch(
  input: string | URL,
  init?: LongRunningFetchInit
): Promise<Response> {
  const timeoutMs = resolveTimeoutMs(init?.timeoutMs);
  const { timeoutMs: _omit, signal, ...rest } = init ?? {};

  const abortSignal =
    signal ??
    (timeoutMs > 0
      ? AbortSignal.timeout(timeoutMs)
      : undefined);

  const res = await undiciFetch(input, {
    ...(rest as UndiciRequestInit),
    ...(abortSignal ? { signal: abortSignal } : {}),
    dispatcher: DISPATCHER,
  });

  return res as unknown as Response;
}

/** Prefer for generic API proxies (dashboard, CRUD, KB). */
export async function backendProxyFetch(
  input: string | URL,
  init?: LongRunningFetchInit
): Promise<Response> {
  return longRunningFetch(input, {
    ...init,
    timeoutMs: init?.timeoutMs ?? BACKEND_PROXY_TIMEOUT_MS,
  });
}

export { PROPOSAL_STAGE_FALLBACK_TIMEOUT_MS };
