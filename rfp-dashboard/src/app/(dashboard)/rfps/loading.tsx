import { DashboardHeader } from "@/components/DashboardHeader";
import { RfpTableSkeleton } from "@/components/loading/RfpTableSkeleton";

export default function RfpsLoading() {
  return (
    <div className="space-y-10">
      <DashboardHeader
        title="Active RFPs"
        subtitle="All opportunities from JustWin and manual intake. Mark Go RFPs, then draft proposals from the Proposals section in the sidebar."
        showSync={true}
      />
      <RfpTableSkeleton />
    </div>
  );
}
