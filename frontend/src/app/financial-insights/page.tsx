import { FinancialInsightsClient } from "@/financial/components/FinancialInsightsClient";
import {
  parseFinancialTab,
  parseTeamworkView,
} from "@/financial/lib/financial-tab";

export const metadata = {
  title: "Financial Insights & Margin Auditor | ZÖ Agency",
  description: "iWorker Google Sheet ingestion, 6-week roadmap, AI financial audit, and margin tracking.",
};

function firstQuery(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value[0] ?? null;
  return value ?? null;
}

export default async function FinancialInsightsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string | string[]; view?: string | string[] }>;
}) {
  const query = await searchParams;
  return (
    <FinancialInsightsClient
      initialTab={parseFinancialTab(firstQuery(query.tab))}
      initialView={parseTeamworkView(firstQuery(query.view))}
    />
  );
}
