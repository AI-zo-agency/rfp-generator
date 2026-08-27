"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import {
  BookOpen,
  Clock3,
  Database,
  FolderKanban,
  LayoutDashboard,
  PanelLeft,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { FinancialTabId } from "../lib/financial-tab";

export type { FinancialTabId };

export interface FinancialNavTab {
  id: FinancialTabId;
  label: string;
  hint: string;
  Icon: LucideIcon;
}

export const FINANCIAL_TABS: FinancialNavTab[] = [
  {
    id: "agency",
    label: "Agency",
    hint: "Jobs with delivery and money",
    Icon: LayoutDashboard,
  },
  {
    id: "quickbooks",
    label: "QuickBooks Ledger",
    hint: "Books, cash, and P&L",
    Icon: BookOpen,
  },
  {
    id: "teamwork",
    label: "Teamwork Projects",
    hint: "Delivery, hours, and risk",
    Icon: FolderKanban,
  },
  {
    id: "iworker",
    label: "iWorker Ingestion & Logs",
    hint: "Timesheets and contractor spend",
    Icon: Clock3,
  },
  {
    id: "ai",
    label: "AI Audit Queue & Insights",
    hint: "Exceptions waiting for review",
    Icon: Sparkles,
  },
  {
    id: "sources",
    label: "Data Sources Inventory",
    hint: "What is connected, and how fresh",
    Icon: Database,
  },
];

const STORAGE_KEY = "zo_financial_nav_collapsed";
const collapsedListeners = new Set<() => void>();

export function readStoredNavCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function persistNavCollapsed(collapsed: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    /* private mode / blocked storage */
  }
  collapsedListeners.forEach((listener) => listener());
}

function subscribeNavCollapsed(onStoreChange: () => void) {
  collapsedListeners.add(onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    collapsedListeners.delete(onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function subscribeNarrow(onStoreChange: () => void) {
  const mq = window.matchMedia("(max-width: 767px)");
  mq.addEventListener("change", onStoreChange);
  return () => mq.removeEventListener("change", onStoreChange);
}

function getNarrowSnapshot() {
  return window.matchMedia("(max-width: 767px)").matches;
}

interface FinancialNavSidebarProps {
  activeTab: string;
  onChange: (id: FinancialTabId) => void;
  mobileOpen: boolean;
  onMobileOpenChange: (open: boolean) => void;
}

function RailTooltip({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={10}>
        <p className="font-semibold">{label}</p>
        {hint ? <p className="mt-0.5 text-[11px] opacity-80">{hint}</p> : null}
      </TooltipContent>
    </Tooltip>
  );
}

export function FinancialNavSidebar({
  activeTab,
  onChange,
  mobileOpen,
  onMobileOpenChange,
}: FinancialNavSidebarProps) {
  const isNarrow = useSyncExternalStore(subscribeNarrow, getNarrowSnapshot, () => false);
  const collapsed = useSyncExternalStore(
    subscribeNavCollapsed,
    readStoredNavCollapsed,
    () => false,
  );

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMobileOpenChange(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen, onMobileOpenChange]);

  const railCollapsed = collapsed && !isNarrow;
  const toggleLabel = railCollapsed ? "Open sidebar" : "Close sidebar";

  const selectTab = (id: FinancialTabId) => {
    onChange(id);
    if (isNarrow) onMobileOpenChange(false);
  };

  const toggleButton = (
    <button
      type="button"
      onClick={() => persistNavCollapsed(!collapsed)}
      aria-expanded={!railCollapsed}
      aria-label={toggleLabel}
      className={cn(
        "fin-rail-toggle hidden h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-foreground md:flex",
        "transition-[background-color,transform,color] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)]",
        "hover:bg-[#3C5A56]/10 hover:text-[#3C5A56]",
        "focus-visible:ring-2 focus-visible:ring-[#3C5A56] focus-visible:ring-offset-2",
        "active:scale-[0.97]",
      )}
    >
      <PanelLeft className="h-[18px] w-[18px]" strokeWidth={1.7} />
    </button>
  );

  return (
    <>
      {mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-[#1e3632]/35 md:hidden"
          aria-label="Close financial sections"
          onClick={() => onMobileOpenChange(false)}
        />
      ) : null}

      <div
        className={cn(
          "relative h-full max-md:w-0 max-md:shrink-0 md:sticky md:top-0 md:self-start",
          "md:transition-[width] md:duration-200 md:ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:md:transition-none",
          railCollapsed ? "md:w-[56px]" : "md:w-[264px]",
        )}
      >
        <aside
          data-collapsed={railCollapsed ? "true" : "false"}
          aria-label="Financial sections"
          inert={isNarrow && !mobileOpen}
          className={cn(
            "sidebar-shell fin-rail flex h-full flex-col overflow-hidden border-r",
            "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50 max-md:w-[264px] max-md:shadow-[0_8px_32px_-12px_rgba(15,23,42,0.28)]",
            "max-md:transition-transform max-md:duration-200 max-md:ease-[cubic-bezier(0.32,0.72,0,1)]",
            "motion-reduce:max-md:transition-none",
            "md:absolute md:inset-0 md:w-full",
            mobileOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full",
          )}
        >
          <TooltipProvider delayDuration={180}>
            <div className="flex shrink-0 items-center gap-2 px-3 pt-3 pb-2">
              <RailTooltip label={toggleLabel}>{toggleButton}</RailTooltip>
              <div className="fin-rail-copy min-w-0">
                <p className="font-heading text-[13px] leading-tight font-semibold tracking-tight text-foreground">
                  Financial & Margin Auditor
                </p>
                <p className="mt-0.5 text-[11px] leading-snug text-[var(--zo-text-muted)]">
                  Pick a source to review
                </p>
              </div>
              <button
                type="button"
                onClick={() => onMobileOpenChange(false)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground transition-[background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] hover:bg-[#3C5A56]/10 active:scale-[0.97] md:hidden"
                aria-label="Close financial sections"
              >
                <X className="h-4 w-4" strokeWidth={1.75} />
              </button>
            </div>

            <nav className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 pt-1 pb-3" aria-label="Auditor sources">
              <p className="sidebar-section-label mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-[var(--zo-text-muted)]">
                Sources
              </p>
              <ul className="flex flex-col gap-1" role="tablist" aria-orientation="vertical">
                {FINANCIAL_TABS.map((tab) => {
                  const isActive = tab.id === activeTab;
                  const item = (
                    <button
                      type="button"
                      role="tab"
                      id={`financial-tab-${tab.id}`}
                      aria-selected={isActive}
                      aria-controls={`financial-panel-${tab.id}`}
                      onClick={() => selectTab(tab.id)}
                      className={cn(
                        "fin-rail-item flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-left outline-none",
                        "transition-[color,background-color,transform] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)]",
                        "focus-visible:ring-2 focus-visible:ring-[#3C5A56] focus-visible:ring-offset-2",
                        "active:scale-[0.97]",
                        isActive
                          ? "bg-[#3C5A56] text-white shadow-[0_1px_2px_rgba(15,23,42,0.12),0_8px_16px_-10px_rgba(60,90,86,0.55)]"
                          : "text-[var(--zo-text-secondary)] hover:bg-[#3C5A56]/10 hover:text-foreground",
                      )}
                    >
                      <tab.Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.75} aria-hidden />
                      <span className="fin-rail-copy min-w-0">
                        <span className="block text-[13px] leading-snug font-semibold">{tab.label}</span>
                        <span
                          className={cn(
                            "mt-0.5 block text-[11px] leading-snug",
                            isActive ? "text-white/75" : "text-[var(--zo-text-muted)]",
                          )}
                        >
                          {tab.hint}
                        </span>
                      </span>
                    </button>
                  );

                  if (!railCollapsed) {
                    return <li key={tab.id}>{item}</li>;
                  }

                  return (
                    <li key={tab.id}>
                      <RailTooltip label={tab.label} hint={tab.hint}>
                        {item}
                      </RailTooltip>
                    </li>
                  );
                })}
              </ul>
            </nav>
          </TooltipProvider>
        </aside>
      </div>
    </>
  );
}
