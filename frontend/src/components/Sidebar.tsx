"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ZoLogo } from "./ZoLogo";
import {
  IconAnalytics,
  IconDashboard,
  IconKnowledge,
  IconPipeline,
  IconProposal,
  IconRfp,
} from "./ui/icons";

const workspaceNav = [
  { href: "/", label: "Dashboard", Icon: IconDashboard },
  { href: "/rfps", label: "RFPs", Icon: IconRfp },
  { href: "/proposals", label: "Proposals", Icon: IconProposal },
  { href: "/knowledge-base", label: "Knowledge Base", Icon: IconKnowledge },
  { href: "/pipeline", label: "Pipeline", Icon: IconPipeline },
  { href: "/analytics", label: "Analytics", Icon: IconAnalytics },
];

const adminNav = [{ href: "/analytics", label: "Settings", Icon: IconAnalytics }];

interface SidebarProps {
  collapsed: boolean;
}

export function Sidebar({ collapsed }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      data-collapsed={collapsed}
      className={`sidebar-shell shell-sidebar sticky top-0 flex h-screen shrink-0 flex-col overflow-hidden border-r backdrop-blur-xl ${
        collapsed ? "w-[76px]" : "w-[260px]"
      }`}
    >
      <div className="border-b border-[var(--shell-border)] px-5 py-6">
        <Link href="/" className="flex items-center gap-3">
          <ZoLogo collapsed={collapsed} />
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-6">
        <p className="sidebar-section-label mb-3 px-3 text-[10px] uppercase tracking-[0.28em] text-zo-text-muted">
          Workspace
        </p>
        <div className="space-y-1">
          {workspaceNav.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href ||
                  pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-normal transition-smooth ${
                  active
                    ? "bg-[#ef5018] text-white shadow-[0_8px_24px_rgba(239,80,24,0.25)]"
                    : "shell-nav-link"
                }`}
              >
                <item.Icon className="h-5 w-5 shrink-0" />
                <span className="sidebar-text">{item.label}</span>
              </Link>
            );
          })}
        </div>

        <p className="sidebar-section-label mb-3 mt-8 px-3 text-[10px] uppercase tracking-[0.28em] text-zo-text-muted">
          Administration
        </p>
        <div className="space-y-1">
          {adminNav.map((item) => (
            <Link
              key={item.label}
              href={item.href}
              className="shell-nav-link flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-normal transition-smooth"
            >
              <item.Icon className="h-5 w-5 shrink-0" />
              <span className="sidebar-text">{item.label}</span>
            </Link>
          ))}
        </div>
      </nav>

      {!collapsed && (
        <div className="border-t border-[var(--shell-border)] p-4">
          <div className="zo-panel-teal rounded-2xl p-4">
            <div className="text-[11px] uppercase tracking-[0.24em] text-white/60">
              Human loop
            </div>
            <p className="mt-2 text-sm leading-6 text-white">
              Sync solicitations, review go/no-go, and track each RFP through
              submission.
            </p>
          </div>
        </div>
      )}
    </aside>
  );
}
