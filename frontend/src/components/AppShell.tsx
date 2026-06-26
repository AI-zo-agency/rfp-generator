"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { MarqueeStrip } from "./MarqueeStrip";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    if (!token) {
      router.push("/login");
    } else {
      setIsAuthenticated(true);
    }
  }, [router]);

  if (!isAuthenticated) {
    return null; // Or a loading spinner
  }

  return (
    <div className="shell-app flex min-h-screen">
      <Sidebar collapsed={collapsed} />
      <div className="main-column flex min-w-0 flex-1 flex-col">
        <TopBar
          collapsed={collapsed}
          onToggleSidebar={() => setCollapsed((c) => !c)}
        />
        <MarqueeStrip />
        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-[1480px] px-6 py-8 md:px-10 md:py-10 lg:px-12">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
