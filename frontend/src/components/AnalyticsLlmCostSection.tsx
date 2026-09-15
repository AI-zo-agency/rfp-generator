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

function mapUserSpend(raw: unknown): { email: string; proposalSpentUsd: number }[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((row) => {
      if (!row || typeof row !== "object") return null;
      const r = row as Record<string, unknown>;
      const email = String(r.email ?? "").trim();
      const proposalSpentUsd = Number(r.proposal_spent_usd ?? 0);
      if (!email || !(proposalSpentUsd > 0)) return null;
      return { email, proposalSpentUsd };
    })
    .filter((x): x is { email: string; proposalSpentUsd: number } => Boolean(x));
}

function emptySummary(monthlyBudget: LlmMonthlyBudget | null): LlmCostSummary {
  return {
    totalCostUsd: 0,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    callCount: 0,
    proposalCount: 0,
    unattributedCostUsd: 0,
    unknownNodeCostUsd: 0,
    unknownNodeCalls: 0,
    unknownBreakdown: { byModel: [], byDate: [] },
    byProposal: [],
    byNode: [],
    byModel: [],
    monthlyBudget,
  };
}

/**
 * Analytics LLM section — monthly budget + cost per person only.
 * All-time /summary rollup is temporarily disabled (slow + noisy).
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
        // Lightweight monthly meter only — do not call /api/llm-cost/summary.
        // const res = await fetch("/api/llm-cost/summary", { ... });
        const res = await fetch("/api/llm-cost/monthly-budget", {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(30_000),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: string };
          throw new Error(body.detail || `Monthly budget failed (${res.status})`);
        }
        const monthlyRaw = asRecord(await res.json());
        if (cancelled) return;
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
                proposalByUser: mapUserSpend(monthlyRaw.proposal_by_user),
              };
        setSummary(emptySummary(monthlyBudget));
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
