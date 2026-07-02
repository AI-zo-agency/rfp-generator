/**
 * Server-side fetch for long proposal stages.
 * Node's native fetch uses undici with a 300s (5 min) headersTimeout by default —
 * that aborts Next.js → backend proxies while uvicorn is still working.
 */
import { Agent, fetch as undiciFetch, type RequestInit as UndiciRequestInit } from "undici";
import { PROPOSAL_STAGE_TIMEOUT_MS } from "./proposal-stage-timeout";

const DISPATCHER = new Agent({
  headersTimeout: PROPOSAL_STAGE_TIMEOUT_MS + 120_000,
  bodyTimeout: PROPOSAL_STAGE_TIMEOUT_MS + 120_000,
  connectTimeout: 60_000,
});

export type LongRunningFetchInit = Omit<RequestInit, "signal"> & {
  timeoutMs?: number;
  signal?: AbortSignal;
};

export async function longRunningFetch(
  input: string | URL,
  init?: LongRunningFetchInit
): Promise<Response> {
  const timeoutMs = init?.timeoutMs ?? PROPOSAL_STAGE_TIMEOUT_MS;
  const { timeoutMs: _omit, signal, ...rest } = init ?? {};

  const res = await undiciFetch(input, {
    ...(rest as UndiciRequestInit),
    signal: signal ?? AbortSignal.timeout(timeoutMs),
    dispatcher: DISPATCHER,
  });

  return res as unknown as Response;
}
