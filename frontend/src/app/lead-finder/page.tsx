import { backendJson } from "@/lib/backend-api";
import {
  LeadFinderClient,
  type LeadsPayload,
} from "@/leads/components/LeadFinderClient";

export const metadata = {
  title: "Lead Finder & Outreach Matcher | ZÖ Agency",
  description: "Wave 3 PoC — contact prioritization and outreach prep briefs.",
};

export const dynamic = "force-dynamic";

export default async function LeadFinderPage() {
  const { data, error } = await backendJson<LeadsPayload>("/leads");
  if (!data) {
    return (
      <div className="mx-auto max-w-5xl p-8">
        <p className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Could not reach the backend{error ? `: ${error}` : "."} Start it with{" "}
          <code>python -m app</code> on port 8001.
        </p>
      </div>
    );
  }
  return <LeadFinderClient payload={data} />;
}
