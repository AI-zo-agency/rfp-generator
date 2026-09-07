import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export function RfpTableSkeleton() {
  return (
    <div
      className="flex min-h-[min(22rem,50vh)] flex-col items-center justify-center py-16"
      role="status"
      aria-busy="true"
      aria-label="Loading RFPs"
    >
      <ZoAmuletLoader label="Loading RFPs" />
    </div>
  );
}
