import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";

export default function ProposalsLoading() {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ProposalsWorkspaceSkeleton />
    </div>
  );
}
