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
    <div className="shell-app flex min-h-screen">
      <Sidebar collapsed={collapsed} />
      <div className="main-column flex min-w-0 flex-1 flex-col">
        <TopBar
          collapsed={collapsed}
          onToggleSidebar={() => setCollapsed((c) => !c)}
        />
        <main className="flex-1 overflow-auto">
          <div
            className={
              isProposalsWorkspace
                ? "px-2 py-3 sm:px-3 sm:py-4 md:px-4 md:py-5"
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
