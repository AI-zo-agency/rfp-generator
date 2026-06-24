import { DashboardHeader } from "@/components/DashboardHeader";
import { RfpTable } from "@/components/RfpTable";
import { getRfps } from "@/lib/rfp-service";

export default async function RfpsPage() {
  const allRfps = await getRfps();
  const rfps = allRfps.filter(
    (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return (
    <div className="space-y-10">
      <DashboardHeader
        title="Active RFPs"
        subtitle="All opportunities from JustWin and manual intake. Mark Go RFPs, then draft proposals from the Proposals section in the sidebar."
        showSync={true}
      />

      <RfpTable rfps={rfps} />
    </div>
  );
}
