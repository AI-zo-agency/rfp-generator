import { Suspense } from "react";
import { DashboardContent } from "@/components/DashboardContent";
import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";
import { getDashboardData } from "@/lib/rfp-service";

async function DashboardData() {
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

function DashboardAmuletFallback() {
  return (
    <div
      className="flex min-h-[min(32rem,70vh)] flex-col items-center justify-center py-20"
      role="status"
      aria-busy="true"
      aria-label="Loading dashboard"
    >
      <ZoAmuletLoader label="Loading dashboard" />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<DashboardAmuletFallback />}>
      <DashboardData />
    </Suspense>
  );
}
