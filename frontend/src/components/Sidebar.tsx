"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ZoLogo } from "./ZoLogo";
import {
  IconAnalytics,
  IconDashboard,
  IconKnowledge,
  IconProposal,
  IconRfp,
} from "./ui/icons";

const workspaceNav = [
  { href: "/", label: "Dashboard", Icon: IconDashboard },
  { href: "/rfps", label: "RFPs", Icon: IconRfp, prefetch: false },
  { href: "/proposals", label: "Proposals", Icon: IconProposal, prefetch: false },
  { href: "/knowledge-base", label: "Knowledge Base", Icon: IconKnowledge },
  { href: "/analytics", label: "Analytics", Icon: IconAnalytics },
];

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
                prefetch={"prefetch" in item ? item.prefetch : undefined}
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
      </nav>
    </aside>
  );
}
