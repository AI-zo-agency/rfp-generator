"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { smoothTransition } from "@/lib/motion";

export interface TabItem {
  id: string;
  label: string;
  count?: number;
}

interface OutlineTabsProps {
  tabs: TabItem[];
  activeTab: string;
  onChange: (id: string) => void;
  className?: string;
  /** Tighter padding and sentence case — legacy pill control */
  compact?: boolean;
  /** Underline tabs (proposal workspace) vs orange pill (elsewhere) */
  variant?: "pill" | "underline";
  /** Stretch tabs across the row */
  fullWidth?: boolean;
}

export function OutlineTabs({
  tabs,
  activeTab,
  onChange,
  className = "",
  compact = false,
  variant = "pill",
  fullWidth = false,
}: OutlineTabsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });
  const [ready, setReady] = useState(false);

  const isUnderline = variant === "underline";

  const updateIndicator = useCallback(() => {
    if (isUnderline) return;
    const el = tabRefs.current.get(activeTab);
    const container = containerRef.current;
    if (!el || !container) return;

    const containerRect = container.getBoundingClientRect();
    const tabRect = el.getBoundingClientRect();
    setIndicator({
      left: tabRect.left - containerRect.left,
      width: tabRect.width,
    });
    setReady(true);
  }, [activeTab, isUnderline]);

  useEffect(() => {
    updateIndicator();
    const ro = new ResizeObserver(updateIndicator);
    if (containerRef.current) ro.observe(containerRef.current);
    window.addEventListener("resize", updateIndicator);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", updateIndicator);
    };
  }, [updateIndicator, tabs]);

  const shellClass = isUnderline
    ? `outline-tabs-underline flex gap-1 border-b border-zo-border/80 sm:gap-2 ${fullWidth ? "w-full" : ""}`
    : compact
      ? "relative inline-flex max-w-full gap-0.5 rounded-lg border border-zo-border/80 bg-[#f8f9f8] p-0.5"
      : "relative inline-flex gap-2 rounded-xl border border-zo-border bg-white p-1.5 shadow-sm";

  const tabClass = isUnderline
    ? "outline-tab-underline relative flex flex-1 items-center justify-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors -mb-px sm:flex-none sm:justify-start sm:px-4"
    : compact
      ? "relative z-10 rounded-md px-3 py-1.5 text-xs font-semibold tracking-normal normal-case"
      : "relative z-10 flex items-center gap-2.5 rounded-lg px-6 py-3 text-xs font-cabin font-semibold uppercase tracking-[0.08em]";

  const indicatorInset = compact ? "top-0.5 bottom-0.5" : "top-1 bottom-1";

  return (
    <div ref={containerRef} className={`${shellClass} ${className}`} role="tablist">
      {!isUnderline && ready && (
        <motion.div
          className={`pointer-events-none absolute rounded-lg bg-[#ef5018] ${indicatorInset}`}
          initial={false}
          animate={{
            left: indicator.left,
            width: indicator.width,
          }}
          transition={smoothTransition}
          style={{ position: "absolute" }}
        />
      )}

      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;
        const underlineActive =
          "border-[#ef5018] text-[#ef5018]";
        const underlineIdle =
          "border-transparent text-zo-text-secondary hover:border-zo-border hover:text-foreground";

        return (
          <button
            key={tab.id}
            ref={(el) => {
              if (el) tabRefs.current.set(tab.id, el);
            }}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(tab.id)}
            className={`${tabClass} flex items-center gap-1.5 transition-smooth ${
              isUnderline
                ? isActive
                  ? underlineActive
                  : underlineIdle
                : isActive
                  ? "text-white"
                  : "text-zo-text-secondary hover:text-foreground"
            }`}
          >
            {tab.label}
            {tab.count !== undefined && tab.count > 0 && (
              <span
                className={
                  isUnderline
                    ? `min-w-[1.125rem] rounded-full px-1.5 py-0.5 text-center text-[10px] font-bold tabular-nums ${
                        isActive
                          ? "bg-[#ef5018]/15 text-[#c2410c]"
                          : "bg-amber-100 text-amber-900"
                      }`
                    : `min-w-[1.25rem] px-2 py-0.5 text-center text-[10px] font-bold transition-smooth rounded-full ${
                        isActive
                          ? "bg-white/20 text-white"
                          : "bg-red-100 text-red-800"
                      }`
                }
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

interface TabPanelProps {
  id: string;
  activeTab: string;
  children: React.ReactNode;
  className?: string;
}

export function TabPanel({
  id,
  activeTab,
  children,
  className = "",
}: TabPanelProps) {
  if (id !== activeTab) return null;
  return (
    <div role="tabpanel" className={className}>
      {children}
    </div>
  );
}
