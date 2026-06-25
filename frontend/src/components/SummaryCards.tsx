import type { DashboardStats } from "@/types/rfp";

interface SummaryCardsProps {
  stats: DashboardStats;
  totalRfps: number;
  publicSector: number;
  subconsultant: number;
}

function CardIcon({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-12 w-12 items-center justify-center bg-[#ef5018]/15 text-[#ef5018]">
      {children}
    </div>
  );
}

export function SummaryCards({
  stats,
  totalRfps,
  publicSector,
  subconsultant,
}: SummaryCardsProps) {
  const cards = [
    {
      label: "Total RFPs",
      value: totalRfps,
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
        </svg>
      ),
    },
    {
      label: "Public Sector",
      value: publicSector,
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
        </svg>
      ),
    },
    {
      label: "Active Pipeline",
      value: stats.activeRfps,
      sub: `${subconsultant} subconsultant · ${stats.pendingGoNoGo} pending`,
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
        </svg>
      ),
    },
  ];

  return (
    <div className="grid gap-5 sm:grid-cols-3">
      {cards.map((card) => (
        <div
          key={card.label}
          className="zo-card flex items-center gap-5 p-6 transition-shadow duration-200 hover:shadow-md md:p-7"
        >
          <CardIcon>{card.icon}</CardIcon>
          <div>
            <p className="font-heading text-3xl font-normal tracking-tight text-foreground md:text-4xl">
              {card.value}
            </p>
            <p className="mt-1 text-sm font-medium text-zo-text-secondary">
              {card.label}
            </p>
            {"sub" in card && card.sub && (
              <p className="mt-0.5 text-xs text-zo-text-muted">{card.sub}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
