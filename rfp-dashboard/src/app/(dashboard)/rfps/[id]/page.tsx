import Link from "next/link";
import { Suspense } from "react";
import { RfpDetailContent } from "@/components/RfpDetailContent";
import { RfpDetailSkeleton } from "@/components/loading/RfpDetailSkeleton";

interface RfpDetailPageProps {
  params: Promise<{ id: string }>;
}

export default async function RfpDetailPage({ params }: RfpDetailPageProps) {
  const { id } = await params;

  return (
    <div className="space-y-10">
      <Link
        href="/rfps"
        className="inline-block text-sm font-semibold text-zo-teal transition-colors hover:text-zo-orange"
      >
        ← Back to RFPs
      </Link>

      <Suspense fallback={<RfpDetailSkeleton />}>
        <RfpDetailContent id={id} />
      </Suspense>
    </div>
  );
}
