"use client";

import { AddManualRfpButton } from "./AddManualRfpButton";
import { SyncJustWinButton } from "./SyncJustWinButton";
import { FadeIn } from "./ui/FadeIn";

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
    <FadeIn>
      <header className="flex flex-col gap-6 sm:gap-8 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] uppercase tracking-[0.34em] text-[#ef5018]">
            OPP-001 / RFP Intelligence
          </p>
          <h1 className="font-heading mt-3 text-3xl leading-tight text-foreground sm:text-4xl md:text-[2.75rem]">
            {title}
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-relaxed text-zo-text-secondary sm:mt-4 md:text-lg">
            {subtitle}
          </p>
        </div>

        {showSync ? (
          <div className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row sm:flex-wrap lg:flex-col lg:items-stretch">
            <AddManualRfpButton variant="header" className="w-full sm:w-auto" />
            <SyncJustWinButton variant="header" className="w-full sm:w-auto" />
          </div>
        ) : null}
      </header>
    </FadeIn>
  );
}
