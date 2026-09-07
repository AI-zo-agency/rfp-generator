"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { motion } from "motion/react";
import { ZoLogo } from "@/components/ZoLogo";
import { IconSwitch } from "@/components/ui/icons";
import { expoOutEase } from "@/lib/motion";
import { prefersReducedMotion } from "../lib/fin-motion";
import "./QuickBooksLedger.css";

export function FinancialShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const headerRef = useRef<HTMLElement>(null);

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

  useGSAP(
    () => {
      if (!isAuthenticated || !headerRef.current || prefersReducedMotion()) return;
      gsap.fromTo(
        headerRef.current.querySelectorAll("[data-fin-shell]"),
        { opacity: 0, y: -14 },
        { opacity: 1, y: 0, duration: 0.55, stagger: 0.08, ease: "power3.out" },
      );
    },
    { dependencies: [isAuthenticated], scope: headerRef },
  );

  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  };

  if (!isAuthenticated) {
    return (
      <div className="flex h-dvh w-full items-center justify-center bg-[var(--zo-bg)]">
        <motion.div
          className="flex flex-col items-center gap-4"
          initial={{ opacity: 0, scale: 0.92 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5, ease: expoOutEase }}
        >
          <div className="h-10 w-10 animate-spin rounded-full border-[3px] border-[#3C5A56] border-t-transparent" />
          <span className="text-sm font-medium tracking-widest uppercase text-[var(--zo-text-muted)]">
            ZO AGENCY
          </span>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="shell-app flex h-dvh max-h-dvh flex-col overflow-clip">
      <header
        ref={headerRef}
        className="shell-header z-30 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-5 py-2.5 md:px-8"
      >
        <div className="flex items-center gap-4" data-fin-shell>
          <Link href="/choose" className="flex items-center gap-3">
            <ZoLogo size="compact" />
          </Link>
          <span className="hidden items-center gap-2 rounded-full border border-[#3C5A56]/25 bg-[#3C5A56]/[0.07] px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] text-[#3C5A56] sm:inline-flex">
            Financial Workspace
          </span>
        </div>

        <div className="flex items-center gap-2 md:gap-3" data-fin-shell>
          <Link href="/choose" className="zo-btn secondary !py-3" aria-label="Switch workspace">
            <IconSwitch className="h-4 w-4" />
            <span className="hidden sm:inline">Switch Workspace</span>
          </Link>
          <button type="button" onClick={handleLogout} className="zo-btn secondary !py-3 cursor-pointer">
            Logout
          </button>
        </div>
      </header>

      <motion.main
        className="flex min-h-0 flex-1 overflow-clip"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.45, delay: 0.12, ease: expoOutEase }}
      >
        {children}
      </motion.main>
    </div>
  );
}
