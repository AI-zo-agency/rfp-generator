import { AddManualRfpButton } from "./AddManualRfpButton";
import { SyncJustWinButton } from "./SyncJustWinButton";

interface DashboardHeaderProps {
  title: string;
  subtitle: string;
  showSync?: boolean;
}

export function DashboardHeader({
  title,
  subtitle,
  showSync = true,
}: DashboardHeaderProps) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-8">
      <div>
        <p className="text-[11px] uppercase tracking-[0.34em] text-[#ef5018]">
          OPP-001 / RFP Intelligence
        </p>
        <h1 className="font-heading mt-3 text-4xl leading-none text-foreground md:text-[2.75rem]">
          {title}
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-zo-text-secondary md:text-lg">
          {subtitle}
        </p>
      </div>

      {showSync && (
        <div className="flex flex-wrap items-center gap-3">
          <AddManualRfpButton variant="header" />
          <SyncJustWinButton variant="header" />
        </div>
      )}
    </header>
  );
}
