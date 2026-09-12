import { AnalyticsLlmCostSection } from "@/components/AnalyticsLlmCostSection";
import { DashboardHeader } from "@/components/DashboardHeader";
import { StatCard } from "@/components/StatCard";
import { formatCurrency } from "@/lib/format";
import { getDashboardData } from "@/lib/rfp-service";

export default async function AnalyticsPage() {
  // Stats only — LLM cost rollup is client-fetched so navigation stays instant.
  const { stats } = await getDashboardData();

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
