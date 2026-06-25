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
}

export function OutlineTabs({
  tabs,
  activeTab,
  onChange,
  className = "",
}: OutlineTabsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });
  const [ready, setReady] = useState(false);

  const updateIndicator = useCallback(() => {
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
  }, [activeTab]);

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

  return (
    <div
      ref={containerRef}
      className={`relative inline-flex gap-1 rounded-xl border border-zo-border bg-white p-1 shadow-sm ${className}`}
      role="tablist"
    >
      {ready && (
        <motion.div
          className="pointer-events-none absolute top-1 bottom-1 rounded-lg bg-[#ef5018]"
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
            className={`relative z-10 flex items-center gap-2 rounded-lg px-5 py-2.5 text-xs font-cabin font-semibold uppercase tracking-[0.08em] transition-smooth ${
              isActive
                ? "text-white"
                : "text-zo-text-secondary hover:text-foreground"
            }`}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span
                className={`px-2 py-0.5 text-[10px] font-bold transition-smooth rounded-full ${
                  isActive
                    ? "bg-zo-orange text-white"
                    : "bg-[var(--zo-surface)] text-zo-text-muted"
                }`}
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
