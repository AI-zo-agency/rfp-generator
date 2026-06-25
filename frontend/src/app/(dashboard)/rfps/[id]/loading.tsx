import Link from "next/link";
import { RfpDetailSkeleton } from "@/components/loading/RfpDetailSkeleton";

export default function RfpDetailLoading() {
  return (
    <div className="space-y-10">
      <Link
        href="/rfps"
        className="inline-block text-sm font-semibold text-zo-teal transition-colors hover:text-zo-orange"
      >
        ← Back to RFPs
      </Link>
      <RfpDetailSkeleton />
    </div>
  );
}
