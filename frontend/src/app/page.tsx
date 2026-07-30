"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    router.replace(token ? "/choose" : "/login");
  }, [router]);

  return (
    <div className="flex h-dvh w-full items-center justify-center bg-[var(--zo-bg)]">
      <div className="flex flex-col items-center gap-4">
        <div className="h-10 w-10 animate-spin rounded-full border-[3px] border-[var(--zo-primary)] border-t-transparent" />
        <span className="text-sm font-medium tracking-widest uppercase text-[var(--zo-text-muted)]">
          ZO AGENCY
        </span>
      </div>
    </div>
  );
}
