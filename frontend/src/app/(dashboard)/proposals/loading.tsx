import { DashboardHeader } from "@/components/DashboardHeader";
import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";

export default function ProposalsLoading() {
  return (
    <div className="space-y-6">
      <DashboardHeader
        title="Proposals"
        subtitle="Draft Go / Go With Conditions RFPs — generate static Sections 1–3 from the knowledge base, then Sections 4–5."
        showSync={false}
      />
      <ProposalsWorkspaceSkeleton />
    </div>
  );
}
