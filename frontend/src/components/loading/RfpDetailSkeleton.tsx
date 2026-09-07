import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export function RfpDetailSkeleton() {
  return (
    <div
      className="flex min-h-[min(28rem,60vh)] flex-col items-center justify-center gap-6 py-16"
      role="status"
      aria-busy="true"
      aria-label="Loading RFP"
    >
      <ZoAmuletLoader label="Loading RFP" />
      <p className="text-xs tracking-wide text-zo-text-muted">Loading RFP…</p>
    </div>
  );
}
