import { Suspense } from "react";
import { ProposalsWorkspace } from "@/components/ProposalsWorkspace";
import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";
import { getRfps } from "@/lib/rfp-service";

export async function ProposalsWorkspaceLoader() {
  const allRfps = await getRfps();
  const goRfps = allRfps.filter(
    (r) =>
      (r.goNoGo === "go" || r.goNoGo === "review") &&
      !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return (
    <Suspense fallback={<ProposalsWorkspaceSkeleton />}>
      <ProposalsWorkspace goRfps={goRfps} />
    </Suspense>
  );
}
