export interface LlmCostProposalRow {
  rfpId: string;
  title: string;
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  calls: number;
  runCount: number;
}

export interface LlmCostStageRow {
  nodeName: string;
  costUsd: number;
  calls: number;
}

export interface LlmCostModelRow {
  model: string;
  costUsd: number;
  calls: number;
}

export interface LlmCostUnknownRow {
  model?: string;
  date?: string;
  costUsd: number;
  calls: number;
}

export interface LlmCostUnknownBreakdown {
  byModel: LlmCostUnknownRow[];
  byDate: LlmCostUnknownRow[];
}

export interface LlmCostSummary {
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  callCount: number;
  proposalCount: number;
  unattributedCostUsd: number;
  unknownNodeCostUsd: number;
  unknownNodeCalls: number;
  unknownBreakdown: LlmCostUnknownBreakdown;
  byProposal: LlmCostProposalRow[];
  byNode: LlmCostStageRow[];
  byModel: LlmCostModelRow[];
}

export interface LlmCostRunRow {
  runId: string;
  runType: string;
  primaryNode: string;
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  calls: number;
  byNode: LlmCostStageRow[];
}

export interface LlmCostRfpBreakdown {
  rfpId: string;
  title: string;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  callCount: number;
  runCount: number;
  byNode: LlmCostStageRow[];
  byRun: LlmCostRunRow[];
}

function asString(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function asUnknownList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function mapProposal(row: Record<string, unknown>): LlmCostProposalRow {
  return {
    rfpId: asString(row.rfp_id),
    title: asString(row.title),
    costUsd: Number(row.cost_usd ?? 0),
    inputTokens: Number(row.input_tokens ?? 0),
    outputTokens: Number(row.output_tokens ?? 0),
    calls: Number(row.calls ?? 0),
    runCount: Number(row.run_count ?? 0),
  };
}

function mapStage(row: Record<string, unknown>): LlmCostStageRow {
  return {
    nodeName: asString(row.node_name, "unknown"),
    costUsd: Number(row.cost_usd ?? 0),
    calls: Number(row.calls ?? 0),
  };
}

function mapModel(row: Record<string, unknown>): LlmCostModelRow {
  return {
    model: asString(row.model, "unknown"),
    costUsd: Number(row.cost_usd ?? 0),
    calls: Number(row.calls ?? 0),
  };
}

function mapUnknownRow(row: Record<string, unknown>): LlmCostUnknownRow {
  return {
    model: row.model != null ? asString(row.model) : undefined,
    date: row.date != null ? asString(row.date) : undefined,
    costUsd: Number(row.cost_usd ?? 0),
    calls: Number(row.calls ?? 0),
  };
}

function mapRun(row: Record<string, unknown>): LlmCostRunRow {
  return {
    runId: asString(row.run_id, "unknown"),
    runType: asString(row.run_type, "other"),
    primaryNode: asString(row.primary_node, "unknown"),
    costUsd: Number(row.cost_usd ?? 0),
    inputTokens: Number(row.input_tokens ?? 0),
    outputTokens: Number(row.output_tokens ?? 0),
    calls: Number(row.calls ?? 0),
    byNode: (Array.isArray(row.by_node) ? row.by_node : []).map((r) =>
      mapStage(r as Record<string, unknown>)
    ),
  };
}

export async function getLlmCostSummary(): Promise<LlmCostSummary | null> {
  try {
    const backendBase =
      process.env.NEXT_PUBLIC_BACKEND_URL ||
      process.env.BACKEND_URL ||
      "http://localhost:8001";
    const res = await fetch(`${backendBase.replace(/\/$/, "")}/api/v1/llm-cost/summary`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      console.warn("[llm-cost] summary unavailable:", res.status);
      return null;
    }
    const data = (await res.json()) as Record<string, unknown>;
    if (!data || typeof data !== "object") {
      console.warn("[llm-cost] invalid summary payload");
      return null;
    }
    const unknownBreakdown = asRecord(data.unknown_breakdown);
    return {
      totalCostUsd: Number(data.total_cost_usd ?? 0),
      totalInputTokens: Number(data.total_input_tokens ?? 0),
      totalOutputTokens: Number(data.total_output_tokens ?? 0),
      callCount: Number(data.call_count ?? 0),
      proposalCount: Number(data.proposal_count ?? 0),
      unattributedCostUsd: Number(data.unattributed_cost_usd ?? 0),
      unknownNodeCostUsd: Number(data.unknown_node_cost_usd ?? 0),
      unknownNodeCalls: Number(data.unknown_node_calls ?? 0),
      unknownBreakdown: {
        byModel: asUnknownList(unknownBreakdown.by_model).map((r) =>
          mapUnknownRow(r as Record<string, unknown>),
        ),
        byDate: asUnknownList(unknownBreakdown.by_date).map((r) =>
          mapUnknownRow(r as Record<string, unknown>),
        ),
      },
      byProposal: asUnknownList(data.by_proposal).map((r) =>
        mapProposal(r as Record<string, unknown>),
      ),
      byNode: asUnknownList(data.by_node).map((r) =>
        mapStage(r as Record<string, unknown>),
      ),
      byModel: asUnknownList(data.by_model).map((r) =>
        mapModel(r as Record<string, unknown>),
      ),
    };
  } catch (error) {
    console.warn("[llm-cost] summary unavailable:", error);
    return null;
  }
}

export async function getLlmCostForRfp(
  rfpId: string
): Promise<LlmCostRfpBreakdown | null> {
  try {
    const safeId = encodeURIComponent(rfpId);
    const res = await fetch(`/api/llm-cost/rfps/${safeId}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      console.warn("[llm-cost] rfp breakdown unavailable:", res.status);
      return null;
    }
    const data = (await res.json()) as Record<string, unknown>;
    if (!data || typeof data !== "object") {
      console.warn("[llm-cost] invalid rfp breakdown payload");
      return null;
    }
    if (typeof data.error === "string" && data.error.trim()) {
      console.warn("[llm-cost] rfp breakdown error:", data.error);
      return null;
    }
    const detailedRuns = Array.isArray(data.by_run_detailed) ? data.by_run_detailed : [];
    return {
      rfpId: asString(data.rfp_id, rfpId),
      title: asString(data.title),
      totalCostUsd: Number(data.total_cost_usd ?? 0),
      totalInputTokens: Number(data.total_input_tokens ?? 0),
      totalOutputTokens: Number(data.total_output_tokens ?? 0),
      callCount: Number(data.call_count ?? 0),
      runCount: Number(data.run_count ?? 0),
      byNode: (Array.isArray(data.by_node) ? data.by_node : []).map((r) =>
        mapStage(r as Record<string, unknown>)
      ),
      byRun: detailedRuns.map((r) => mapRun(r as Record<string, unknown>)),
    };
  } catch (error) {
    console.warn("[llm-cost] rfp breakdown unavailable:", error);
    return null;
  }
}
