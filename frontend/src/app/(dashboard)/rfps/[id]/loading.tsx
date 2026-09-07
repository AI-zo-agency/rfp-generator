import Link from "next/link";
import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export default function RfpDetailLoading() {
  return (
    <div className="space-y-10">
      <Link
        href="/rfps"
        className="inline-block text-sm font-semibold text-zo-teal transition-colors hover:text-zo-orange"
      >
        ← Back to RFPs
      </Link>
      <div
        className="flex min-h-[min(28rem,60vh)] flex-col items-center justify-center py-16"
        role="status"
        aria-busy="true"
        aria-label="Loading RFP"
      >
        <ZoAmuletLoader label="Loading RFP" />
      </div>
    </div>
  );
}
