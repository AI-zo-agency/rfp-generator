import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runPhase2Retrieval } from "./proposal-api";

const START = Date.parse("2026-10-27T18:02:54Z");
const MIN = 60 * 1000;

type ServerState = {
  checkpoint: Record<string, unknown>;
  job: Record<string, unknown> | null;
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

function stubServer(stateAt: (elapsedMs: number) => ServerState) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const state = stateAt(Date.now() - START);
    if (init?.method === "POST") {
      return jsonResponse({ ok: true, started: true }, 202);
    }
    if (url.endsWith("/proposal/job-status")) {
      return jsonResponse({ job: state.job });
    }
    return jsonResponse({
      draft: null,
      research: {
        rfpId: "r1",
        rfpSections: [],
        evidenceCorpus: [],
        updatedAt: state.checkpoint.updatedAt,
        pipelineCheckpoint: state.checkpoint,
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
}

/** Advance fake time in poll-sized steps until the promise settles. */
async function settle<T>(promise: Promise<T>, maxMs: number) {
  const box: { outcome: { value?: T; error?: unknown } | null } = { outcome: null };
  promise.then(
    (value) => (box.outcome = { value }),
    (error) => (box.outcome = { error })
  );
  const stopAt = Date.now() + maxMs;
  while (!box.outcome && Date.now() < stopAt) {
    await vi.advanceTimersByTimeAsync(4_000);
  }
  return { outcome: box.outcome, elapsedMs: Date.now() - START };
}

const phase2Running = {
  lastCompletedPhase: "sections-1-3",
  inProgressPhase: "phase-2",
  // Frozen: LangExtract runs ~18 min without touching the checkpoint.
  updatedAt: "2026-10-27T18:02:55Z",
};

describe("waitForProposalPhase (via runPhase2Retrieval)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: START, toFake: ["setTimeout", "clearTimeout", "Date"] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps waiting past 22 min while the server job is still running", async () => {
    stubServer((elapsed) =>
      elapsed < 30 * MIN
        ? { checkpoint: phase2Running, job: { jobType: "phase-2", status: "running" } }
        : {
            checkpoint: {
              lastCompletedPhase: "phase-2",
              inProgressPhase: null,
              resumeFromPhase: "phase-3",
              updatedAt: "2026-10-27T18:33:24Z",
            },
            job: null,
          }
    );

    const { outcome, elapsedMs } = await settle(runPhase2Retrieval("r1"), 40 * MIN);

    expect(outcome?.error).toBeUndefined();
    expect(outcome?.value?.pipelineCheckpoint?.lastCompletedPhase).toBe("phase-2");
    expect(elapsedMs).toBeGreaterThanOrEqual(30 * MIN);
  });

  it("gives up on a dead backend instead of waiting forever", async () => {
    // Job lock gone (job-status null) and the checkpoint never moves: the
    // client's 15-min stale-checkpoint rule reports it interrupted.
    stubServer(() => ({ checkpoint: phase2Running, job: null }));

    const { outcome, elapsedMs } = await settle(runPhase2Retrieval("r1"), 40 * MIN);

    expect(String(outcome?.error)).toMatch(/interrupted/);
    expect(elapsedMs).toBeLessThan(16 * MIN);
  });

  it("times out after 22 min with no server progress while a predecessor looks stuck", async () => {
    // Checkpoint frozen on Sections 1–3 (not stale-healed: it is not our phase
    // failing) and no job: nothing moves, so the no-progress ceiling ends it.
    stubServer(() => ({
      checkpoint: { ...phase2Running, inProgressPhase: "sections-1-3", lastCompletedPhase: null },
      job: null,
    }));

    const { outcome, elapsedMs } = await settle(runPhase2Retrieval("r1"), 40 * MIN);

    expect(String(outcome?.error)).toMatch(/no server progress/);
    expect(elapsedMs).toBeGreaterThan(22 * MIN);
    expect(elapsedMs).toBeLessThan(24 * MIN);
  });
});
