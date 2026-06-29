import { Suspense } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { RfpTableSection } from "@/components/RfpTableSection";
import { RfpTableSkeleton } from "@/components/loading/RfpTableSkeleton";

export default function RfpsPage() {
  return (
    <div className="space-y-8 sm:space-y-10">
      <DashboardHeader
        title="Active RFPs"
        subtitle="All opportunities from JustWin and manual intake. Mark Go RFPs, then draft proposals from the Proposals section in the sidebar."
        showSync={true}
      />

      <Suspense fallback={<RfpTableSkeleton />}>
        <RfpTableSection />
      </Suspense>
    </div>
  );
}
