import { backendJson } from "@/lib/backend-api";
import {
  LeadFinderClient,
  type LeadsPayload,
} from "@/leads/components/LeadFinderClient";

export const metadata = {
  title: "Prospect Operations | ZÖ Agency",
  description: "HubSpot-backed prospect prioritization, research, and human-led outreach preparation.",
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
