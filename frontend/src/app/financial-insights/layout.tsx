import { FinancialShell } from "@/financial/components/FinancialShell";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default function FinancialInsightsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <FinancialShell>{children}</FinancialShell>;
}
