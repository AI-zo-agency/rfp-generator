"use client";

import type { ReactNode } from "react";

interface FinancialHeaderProps {
  title: string;
  subtitle: string;
  trailing?: ReactNode;
}

export function FinancialHeader({ title, subtitle, trailing }: FinancialHeaderProps) {
  return (
    <header className="flex min-w-0 shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:gap-5">
      <div className="min-w-0 shrink-0">
        <h1 className="font-heading text-[1.125rem] leading-none tracking-tight text-foreground">
          {title}
        </h1>
        <p className="sr-only">{subtitle}</p>
      </div>
      {trailing ? <div className="min-w-0 flex-1">{trailing}</div> : null}
    </header>
  );
}
