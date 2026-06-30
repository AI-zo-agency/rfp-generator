import { Suspense } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { ProposalsWorkspaceLoader } from "@/components/ProposalsWorkspaceLoader";
import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";

export default function ProposalsPage() {
  return (
    <div className="space-y-3 sm:space-y-4">
      <DashboardHeader
        title="Proposals"
        subtitle="Draft Go RFPs — full-width editor with section outlines, AI revisions, and export."
        showSync={false}
      />

      <Suspense fallback={<ProposalsWorkspaceSkeleton />}>
        <ProposalsWorkspaceLoader />
      </Suspense>
    </div>
  );
}
