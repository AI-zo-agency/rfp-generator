"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ZoLogo } from "@/components/ZoLogo";
import { IconSwitch } from "@/components/ui/icons";
import "./QuickBooksLedger.css";

export function FinancialShell({ children }: { children: React.ReactNode }) {
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

  useEffect(() => {
    document.documentElement.classList.add("fin-lock");
    return () => document.documentElement.classList.remove("fin-lock");
  }, []);

  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  };

  if (!isAuthenticated) {
    return (
      <div className="flex h-dvh w-full items-center justify-center bg-[var(--zo-bg)]">
        <div className="flex flex-col items-center gap-4">
          <div className="h-10 w-10 animate-spin rounded-full border-[3px] border-[#3C5A56] border-t-transparent" />
          <span className="text-sm font-medium tracking-widest uppercase text-[var(--zo-text-muted)]">
            ZO AGENCY
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="shell-app flex h-dvh max-h-dvh flex-col overflow-clip">
      <header className="shell-header z-30 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-5 py-2.5 md:px-8">
        <div className="flex items-center gap-4">
          <Link href="/choose" className="flex items-center gap-3">
            <ZoLogo size="compact" />
          </Link>
          <span className="hidden items-center gap-2 rounded-full border border-[#3C5A56]/25 bg-[#3C5A56]/[0.07] px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] text-[#3C5A56] sm:inline-flex">
            Financial Workspace
          </span>
        </div>

        <div className="flex items-center gap-2 md:gap-3">
          <Link href="/choose" className="zo-btn secondary !py-3" aria-label="Switch workspace">
            <IconSwitch className="h-4 w-4" />
            <span className="hidden sm:inline">Switch Workspace</span>
          </Link>
          <button type="button" onClick={handleLogout} className="zo-btn secondary !py-3 cursor-pointer">
            Logout
          </button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-clip">
        <div className="mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 flex-col px-5 pt-3 pb-3 sm:px-6 md:px-8">
          {children}
        </div>
      </main>
    </div>
  );
}
