import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export default function RfpDashboardLoading() {
  return (
    <div
      className="flex min-h-[min(32rem,70vh)] flex-col items-center justify-center py-20"
      role="status"
      aria-busy="true"
      aria-label="Loading dashboard"
    >
      <ZoAmuletLoader label="Loading dashboard" />
    </div>
  );
}
