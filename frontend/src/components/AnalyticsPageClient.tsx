"use client";

import { useEffect, useState } from "react";
import { AnalyticsLlmCostSection } from "@/components/AnalyticsLlmCostSection";
import { DashboardHeader } from "@/components/DashboardHeader";
import { StatCard } from "@/components/StatCard";
import { formatCurrency } from "@/lib/format";
import { computeStats } from "@/lib/mock-rfps";
import type { DashboardStats, RfpRecord } from "@/types/rfp";

const EMPTY_STATS: DashboardStats = {
  activeRfps: 0,
  pendingGoNoGo: 0,
  inProgress: 0,
  dueThisWeek: 0,
  submittedThisMonth: 0,
  winRate: 0,
  pipelineValue: 0,
  avgFitScore: 0,
};

/**
 * Fully client-rendered so clicking Analytics never waits on RSC/data.
 * Soft navigation used to hang for minutes while the server scanned costs.
 */
export function AnalyticsPageClient() {
  const [stats, setStats] = useState<DashboardStats>(EMPTY_STATS);

  useEffect(() => {
    let cancelled = false;
    async function loadStats() {
      try {
        const res = await fetch("/api/rfps/list", {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(20_000),
        });
        if (!res.ok) return;
        const list = (await res.json()) as RfpRecord[];
        if (!cancelled && Array.isArray(list)) setStats(computeStats(list));
      } catch {
        /* keep empty stats */
      }
    }
    void loadStats();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-12">
      <DashboardHeader
        title="Analytics"
        subtitle="RFP pipeline health and LLM usage costs."
        showSync={false}
      />

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard
          label="Monthly Submissions"
          value="~12"
          subtitle="Per writer target"
          accent="teal"
        />
        <StatCard
          label="Close Rate"
          value={`${stats.winRate}%`}
          subtitle="28+ wins/year target"
          accent="orange"
        />
        <StatCard
          label="Pipeline Value"
          value={formatCurrency(stats.pipelineValue)}
          subtitle={`Avg fit · ${stats.avgFitScore}`}
          accent="black"
        />
        <StatCard
          label="Submitted This Month"
          value={stats.submittedThisMonth}
          subtitle="Current period"
          accent="teal"
        />
        <StatCard
          label="Pending Go/No-Go"
          value={stats.pendingGoNoGo}
          subtitle="Awaiting approval"
          accent="orange"
        />
        <StatCard
          label="Due This Week"
          value={stats.dueThisWeek}
          subtitle="Requires attention"
          accent="black"
        />
      </div>

      <AnalyticsLlmCostSection />
    </div>
  );
}
