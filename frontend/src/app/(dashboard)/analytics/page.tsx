import { AnalyticsPageClient } from "@/components/AnalyticsPageClient";

/**
 * Zero server awaits. Parent layout is force-dynamic, but this page itself
 * must not block soft navigation on dashboard/cost fetches.
 */
export default function AnalyticsPage() {
  return <AnalyticsPageClient />;
}
