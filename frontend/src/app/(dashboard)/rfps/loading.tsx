import { DashboardHeader } from "@/components/DashboardHeader";
import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export default function RfpsLoading() {
  return (
    <div className="space-y-10">
      <DashboardHeader
        title="Active RFPs"
        subtitle="All opportunities from JustWin and manual intake. Mark Go RFPs, then draft proposals from the Proposals section in the sidebar."
        showSync={true}
      />
      <div
        className="flex min-h-[min(22rem,50vh)] flex-col items-center justify-center py-16"
        role="status"
        aria-busy="true"
        aria-label="Loading RFPs"
      >
        <ZoAmuletLoader label="Loading RFPs" />
      </div>
    </div>
  );
}
