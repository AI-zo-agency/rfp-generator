"use client";

import { FileSpreadsheet, ShieldAlert, Database, type LucideIcon } from "lucide-react";

export interface FinancialNavItem {
  id: string;
  label: string;
}

interface FinancialSidebarNavProps {
  items: FinancialNavItem[];
  activeId: string;
  onChange: (id: string) => void;
}

const ICONS: Record<string, LucideIcon> = {
  iworker: FileSpreadsheet,
  ai: ShieldAlert,
  sources: Database,
};

export function FinancialSidebarNav({ items, activeId, onChange }: FinancialSidebarNavProps) {
  return (
    <nav
      role="tablist"
      aria-label="Financial dashboard sections"
      className="grid grid-cols-1 gap-2 sm:grid-cols-3 lg:flex lg:w-64 lg:shrink-0 lg:flex-col lg:gap-2"
    >
      {items.map((item) => {
        const Icon = ICONS[item.id] ?? FileSpreadsheet;
        const isActive = item.id === activeId;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(item.id)}
            className={`flex w-full cursor-pointer items-center gap-3 rounded-xl px-4 py-3.5 text-left text-sm font-medium transition-smooth ${
              isActive
                ? "bg-[#3C5A56] text-white shadow-[0_8px_24px_rgba(60,90,86,0.25)]"
                : "border border-zo-border bg-white text-zo-text-secondary hover:border-[#3C5A56]/30 hover:bg-[#3C5A56]/5 hover:text-foreground"
            }`}
          >
            <Icon className="h-5 w-5 shrink-0" />
            <span className="leading-snug">{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
