"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const isProposalsWorkspace = pathname === "/proposals" || pathname.startsWith("/proposals/");

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    if (!token) {
      router.push("/login");
    } else {
      setIsAuthenticated(true);
    }
  }, [router]);

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="shell-app flex h-dvh max-h-dvh overflow-hidden">
      <Sidebar collapsed={collapsed} />
      <div className="main-column flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar
          collapsed={collapsed}
          onToggleSidebar={() => setCollapsed((c) => !c)}
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
                ? "flex min-h-0 flex-1 flex-col overflow-hidden px-2 py-2 sm:px-3 sm:py-3 md:px-4 md:py-4"
                : "mx-auto max-w-[1480px] px-4 py-6 sm:px-6 sm:py-8 md:px-10 md:py-10 lg:px-12"
            }
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
