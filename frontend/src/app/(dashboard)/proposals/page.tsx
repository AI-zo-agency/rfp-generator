import { Suspense } from "react";
import { DashboardHeader } from "@/components/DashboardHeader";
import { ProposalsWorkspace } from "@/components/ProposalsWorkspace";
import { getRfps } from "@/lib/rfp-service";

export default async function ProposalsPage() {
  const allRfps = await getRfps();
  const goRfps = allRfps.filter(
    (r) =>
      (r.goNoGo === "go" || r.goNoGo === "review") &&
      !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return (
    <div className="space-y-6">
      <DashboardHeader
        title="Proposals"
        subtitle="Draft Go / Go With Conditions RFPs — generate static Sections 1–3 from the knowledge base, then Sections 4–5."
        showSync={false}
      />

      <Suspense
        fallback={
          <div className="zo-card p-12 text-center text-sm text-zo-text-muted">
            Loading proposals…
          </div>
        }
      >
        <ProposalsWorkspace goRfps={goRfps} />
      </Suspense>
    </div>
  );
}
