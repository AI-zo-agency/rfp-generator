import { DashboardContent } from "@/components/DashboardContent";
import { getDashboardData } from "@/lib/rfp-service";

export default async function DashboardPage() {
  const { rfps, stats, allRfps } = await getDashboardData();

  return (
    <DashboardContent
      rfps={rfps}
      allRfps={allRfps}
      stats={stats}
    />
  );
}
