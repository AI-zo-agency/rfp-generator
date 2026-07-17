"use client";

import { useRouter } from "next/navigation";

interface TopBarProps {
  collapsed: boolean;
  onToggleSidebar: () => void;
}

export function TopBar({ collapsed, onToggleSidebar }: TopBarProps) {
  const router = useRouter();

  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  };

  return (
    <header className="shell-header sticky top-0 z-30 flex flex-wrap items-center justify-between gap-4 border-b px-6 py-4 backdrop-blur-xl md:px-10">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={onToggleSidebar}
          className="shell-icon-btn flex h-9 w-9 items-center justify-center border transition-colors duration-200"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" d={collapsed ? "M9 5l7 7-7 7" : "M15 19l-7-7 7-7"} />
          </svg>
        </button>
        <div className="text-[11px] uppercase tracking-[0.34em] text-[#ef5018]">
          OPP-001 / RFP Intelligence
        </div>
      </div>

      <div className="flex items-center gap-2 md:gap-3">
        <button type="button" onClick={handleLogout} className="zo-btn secondary !py-3">
          Logout
        </button>
      </div>
    </header>
  );
}
