import { DashboardContent } from "@/components/DashboardContent";
import { mockActivity, mockTeam } from "@/lib/mock-rfps";
import { getDashboardData } from "@/lib/rfp-service";

export default async function DashboardPage() {
  const { rfps, stats, allRfps } = await getDashboardData();

  return (
    <DashboardContent
      rfps={rfps}
      allRfps={allRfps}
      stats={stats}
      activity={mockActivity}
      team={mockTeam}
    />
  );
}
