import { DashboardContent } from "@/components/DashboardContent";
import { getDashboardData } from "@/lib/rfp-service";

export default async function DashboardPage() {
  const {
    rfps,
    stats,
    allRfps,
    recentActivity,
    currentProposals,
    latestProposal,
  } = await getDashboardData();

  return (
    <DashboardContent
      rfps={rfps}
      allRfps={allRfps}
      stats={stats}
      recentActivity={recentActivity}
      currentProposals={currentProposals}
      latestProposal={latestProposal}
    />
  );
}
