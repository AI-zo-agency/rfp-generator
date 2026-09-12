"use client";

import { useEffect, useState } from "react";
import { LlmCostPanel } from "@/components/LlmCostPanel";
import type { LlmCostSummary, LlmMonthlyBudget } from "@/lib/llm-cost-service";

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

function mapSummary(data: Record<string, unknown>): LlmCostSummary {
  const unknownBreakdown = asRecord(data.unknown_breakdown);
  const monthlyRaw = asRecord(data.monthly_budget);
  const monthlyBudget: LlmMonthlyBudget | null =
    Object.keys(monthlyRaw).length === 0
      ? null
      : {
          enabled: Boolean(monthlyRaw.enabled),
          limitUsd: Number(monthlyRaw.limit_usd ?? 0),
          spentUsd: Number(monthlyRaw.spent_usd ?? 0),
          remainingUsd: Number(monthlyRaw.remaining_usd ?? 0),
          blocked: Boolean(monthlyRaw.blocked),
          proposalSpentUsd: Number(monthlyRaw.proposal_spent_usd ?? 0),
          financialSpentUsd: Number(monthlyRaw.financial_spent_usd ?? 0),
          periodStart: asString(monthlyRaw.period_start),
          periodEnd: asString(monthlyRaw.period_end),
          timezone: asString(monthlyRaw.timezone, "UTC"),
        };

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
      byModel: asUnknownList(unknownBreakdown.by_model).map((r) => {
        const row = r as Record<string, unknown>;
        return {
          model: row.model != null ? asString(row.model) : undefined,
          date: row.date != null ? asString(row.date) : undefined,
          costUsd: Number(row.cost_usd ?? 0),
          calls: Number(row.calls ?? 0),
        };
      }),
      byDate: asUnknownList(unknownBreakdown.by_date).map((r) => {
        const row = r as Record<string, unknown>;
        return {
          model: row.model != null ? asString(row.model) : undefined,
          date: row.date != null ? asString(row.date) : undefined,
          costUsd: Number(row.cost_usd ?? 0),
          calls: Number(row.calls ?? 0),
        };
      }),
    },
    byProposal: asUnknownList(data.by_proposal).map((r) => {
      const row = r as Record<string, unknown>;
      return {
        rfpId: asString(row.rfp_id),
        title: asString(row.title),
        costUsd: Number(row.cost_usd ?? 0),
        inputTokens: Number(row.input_tokens ?? 0),
        outputTokens: Number(row.output_tokens ?? 0),
        calls: Number(row.calls ?? 0),
        runCount: Number(row.run_count ?? 0),
      };
    }),
    byNode: asUnknownList(data.by_node).map((r) => {
      const row = r as Record<string, unknown>;
      return {
        nodeName: asString(row.node_name, "unknown"),
        costUsd: Number(row.cost_usd ?? 0),
        calls: Number(row.calls ?? 0),
      };
    }),
    byModel: asUnknownList(data.by_model).map((r) => {
      const row = r as Record<string, unknown>;
      return {
        model: asString(row.model, "unknown"),
        costUsd: Number(row.cost_usd ?? 0),
        calls: Number(row.calls ?? 0),
      };
    }),
    monthlyBudget,
  };
}

/**
 * Loads the heavy LLM cost rollup in the browser so navigating to Analytics
 * is instant. The old server-render awaited a multi-minute Supabase scan.
 */
export function AnalyticsLlmCostSection() {
  const [summary, setSummary] = useState<LlmCostSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/api/llm-cost/summary", {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(45_000),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: string };
          throw new Error(body.detail || `Cost summary failed (${res.status})`);
        }
        const data = (await res.json()) as Record<string, unknown>;
        if (cancelled) return;
        setSummary(mapSummary(data));
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Cost summary unavailable");
        setSummary(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="zo-card p-10">
        <h2 className="font-heading text-2xl font-bold">LLM cost tracking</h2>
        <p className="mt-3 text-base text-zo-text-secondary">
          Loading spend breakdown…
        </p>
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="zo-card p-10">
        <h2 className="font-heading text-2xl font-bold">LLM cost tracking</h2>
        <p className="mt-3 max-w-xl text-base leading-relaxed text-zo-text-secondary">
          {error ||
            "Cost data is unavailable — start the backend and generate or scan a proposal to begin recording usage."}
        </p>
      </div>
    );
  }

  return <LlmCostPanel summary={summary} />;
}
