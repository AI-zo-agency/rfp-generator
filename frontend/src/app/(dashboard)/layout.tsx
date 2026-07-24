import { AppShell } from "@/components/AppShell";
import { PageTransition } from "@/components/PageTransition";

/** RFP list/dashboard must always hit the live backend after create/delete. */
export const dynamic = "force-dynamic";
export const revalidate = 0;

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell>
      <PageTransition>{children}</PageTransition>
    </AppShell>
  );
}
