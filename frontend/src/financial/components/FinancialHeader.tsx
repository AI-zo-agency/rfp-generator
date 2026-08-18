"use client";

import { Menu } from "lucide-react";

interface FinancialHeaderProps {
  title: string;
  subtitle: string;
  onOpenNav?: () => void;
}

export function FinancialHeader({ title, subtitle, onOpenNav }: FinancialHeaderProps) {
  return (
    <header className="flex min-w-0 shrink-0 items-start gap-3">
      {onOpenNav ? (
        <button
          type="button"
          onClick={onOpenNav}
          className="shell-icon-btn mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center border md:hidden"
          aria-label="Open financial sections"
        >
          <Menu className="h-4 w-4" strokeWidth={1.75} />
        </button>
      ) : null}
      <div className="min-w-0">
        <h1 className="font-heading text-[1.125rem] leading-none tracking-tight text-foreground">
          {title}
        </h1>
        <p className="mt-1.5 text-[13px] leading-snug text-[var(--zo-text-secondary)]">{subtitle}</p>
      </div>
    </header>
  );
}
