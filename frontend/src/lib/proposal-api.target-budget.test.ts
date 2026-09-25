import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runPhase3_5Budget } from "./proposal-api";
import type { ProposalBudget, ProposalResearch } from "@/types/proposal";

const budget: ProposalBudget = { rfpId: "r1", updatedAt: "t" } as ProposalBudget;
const research: ProposalResearch = {
  rfpId: "r1",
  rfpSections: [],
  evidenceCorpus: [],
  retrievalRounds: 0,
  coverageThreshold: 0,
  budget,
} as unknown as ProposalResearch;

function mockSyncResponse() {
  return {
    status: 200,
    ok: true,
    text: async () =>
      JSON.stringify({ draft: null, research, budget }),
  } as Response;
}

describe("runPhase3_5Budget request body", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(mockSyncResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes targetBudgetUsd in the POST body when provided", async () => {
    await runPhase3_5Budget("r1", undefined, {
      chainNext: false,
      targetBudgetUsd: 150000,
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toContain('"targetBudgetUsd":150000');
  });

  it("omits the body when called with no options", async () => {
    await runPhase3_5Budget("r1");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });
});
