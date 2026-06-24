import { DashboardHeader } from "@/components/DashboardHeader";
import { StatCard } from "@/components/StatCard";
import { formatCurrency } from "@/lib/format";
import { getDashboardData } from "@/lib/rfp-service";

export default async function AnalyticsPage() {
  const { stats } = await getDashboardData();

  return (
    <div className="space-y-12">
      <DashboardHeader
        title="Analytics"
        subtitle="RFP volume, close rate, and pipeline health."
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

      <div className="zo-card p-10">
        <h2 className="font-heading text-2xl font-bold">Coming Soon</h2>
        <p className="mt-3 max-w-xl text-base leading-relaxed text-zo-text-secondary">
          Win/loss trends, writer performance, and AI weekly briefs.
        </p>
      </div>
    </div>
  );
}
