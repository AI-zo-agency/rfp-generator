import { DashboardHeader } from "@/components/DashboardHeader";
import { LlmCostPanel } from "@/components/LlmCostPanel";
import { StatCard } from "@/components/StatCard";
import { formatCurrency } from "@/lib/format";
import { getLlmCostSummary } from "@/lib/llm-cost-service";
import { getDashboardData } from "@/lib/rfp-service";

export default async function AnalyticsPage() {
  const [{ stats }, llmCost] = await Promise.all([getDashboardData(), getLlmCostSummary()]);

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

      {llmCost ? (
        <LlmCostPanel summary={llmCost} />
      ) : (
        <div className="zo-card p-10">
          <h2 className="font-heading text-2xl font-bold">LLM cost tracking</h2>
          <p className="mt-3 max-w-xl text-base leading-relaxed text-zo-text-secondary">
            Cost data is unavailable — start the backend and generate or scan a proposal to
            begin recording usage.
          </p>
        </div>
      )}
    </div>
  );
}
