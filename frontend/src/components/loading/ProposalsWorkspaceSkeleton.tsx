import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export function ProposalsWorkspaceSkeleton() {
  return (
    <div
      className="flex min-h-[min(28rem,60vh)] flex-col items-center justify-center py-16"
      role="status"
      aria-busy="true"
      aria-label="Loading proposals"
    >
      <ZoAmuletLoader label="Loading proposals" />
    </div>
  );
}
