import { Suspense } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { ProposalsWorkspaceLoader } from "@/components/ProposalsWorkspaceLoader";
import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";

export default function ProposalsPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 sm:gap-3">
      <div className="shrink-0">
      <DashboardHeader
        title="Proposals"
        subtitle="Draft Go RFPs — outlines, review, and export."
        showSync={false}
        compact
      />
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Suspense fallback={<ProposalsWorkspaceSkeleton />}>
          <ProposalsWorkspaceLoader />
        </Suspense>
      </div>
    </div>
  );
}
