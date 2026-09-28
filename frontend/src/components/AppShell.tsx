"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { GlobalJobStatusWidget } from "./GlobalJobStatusWidget";
import { ZoAmuletLoader } from "./ZoAmuletLoader";
import {
  setAnalyticsContext,
  startWorkspaceAnalytics,
  trackClick,
  trackPageView,
} from "@/lib/zo-analytics";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const isProposalsWorkspace =
    pathname === "/proposals" || pathname.startsWith("/proposals/");

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    if (!token) {
      router.push("/login");
    } else {
      setIsAuthenticated(true);
    }
  }, [router]);

  useEffect(() => {
    if (!isAuthenticated) return;
    // Activity / Product analytics pages must not pollute UAT metrics.
    if (pathname === "/activity" || pathname.startsWith("/activity/")) {
      setAnalyticsContext({ path: pathname, tab: "", view: "" });
      return;
    }
    startWorkspaceAnalytics("rfp", { path: pathname });
  }, [isAuthenticated, pathname]);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (pathname === "/activity" || pathname.startsWith("/activity/")) return;
    trackPageView(pathname);
    if (pathname.startsWith("/rfps/") && pathname !== "/rfps") {
      const id = pathname.split("/")[2];
      if (id) {
        trackClick("rfp.open", {
          path: pathname,
          entity_type: "rfp",
          entity_id: id,
          funnel: true,
        });
      }
    }
    if (pathname === "/proposals" || pathname.startsWith("/proposals")) {
      trackClick("proposal.open", { path: pathname, funnel: true });
    }
  }, [isAuthenticated, pathname]);

  if (!isAuthenticated) {
    return <ZoAmuletLoader fullScreen label="Loading ZO Agency" />;
  }

  return (
    <div className="shell-app flex h-dvh max-h-dvh overflow-hidden">
      <Sidebar collapsed={collapsed} />
      <div className="main-column flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar
          collapsed={collapsed}
          onToggleSidebar={() => {
            trackClick("shell.sidebar_toggle");
            setCollapsed((c) => !c);
          }}
        />
        <main
          className={
            isProposalsWorkspace
              ? "flex min-h-0 flex-1 flex-col overflow-hidden"
              : "min-h-0 flex-1 overflow-auto"
          }
        >
          <div
            className={
              isProposalsWorkspace
                ? "flex min-h-0 flex-1 flex-col overflow-hidden px-2 py-1 sm:px-3 sm:py-1.5 md:px-4 md:py-2"
                : "mx-auto max-w-[1480px] px-4 py-6 sm:px-6 sm:py-8 md:px-10 md:py-10 lg:px-12"
            }
          >
            {children}
          </div>
        </main>
      </div>
      <GlobalJobStatusWidget />
    </div>
  );
}
