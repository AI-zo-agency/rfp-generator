import { DashboardHeader } from "@/components/DashboardHeader";
import { ProcessPipeline } from "@/components/ProcessPipeline";
import { RfpTable } from "@/components/RfpTable";
import { TeamWorkload } from "@/components/TeamWorkload";
import { mockTeam } from "@/lib/mock-rfps";
import { getRfps } from "@/lib/rfp-service";

export default async function PipelinePage() {
  const rfps = await getRfps();
  const active = rfps.filter(
    (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return (
    <div className="space-y-12">
      <DashboardHeader
        title="Pipeline"
        subtitle="Track every RFP through the nine-stage workflow."
        showSync={false}
      />

      <ProcessPipeline rfps={active} />

      <RfpTable rfps={active} showFilters={true} />

      <TeamWorkload team={mockTeam} />
    </div>
  );
}
