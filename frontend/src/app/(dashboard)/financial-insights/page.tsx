import { FinancialInsightsClient } from "@/components/financials/FinancialInsightsClient";

export const metadata = {
  title: "Financial Insights & Margin Auditor | ZÖ Agency",
  description: "iWorker Google Sheet ingestion, 6-week roadmap, AI financial audit, and margin tracking.",
};

export default function FinancialInsightsPage() {
  return (
    <div className="p-6 md:p-8 max-w-7xl ml-0 mr-auto w-full">
      <FinancialInsightsClient />
    </div>
  );
}
