"use client";

import { useState } from "react";
import { UserActivityPanel } from "@/components/UserActivityPanel";
import { UserAnalyticsPanel } from "@/components/UserAnalyticsPanel";

export function RfpActivityPageClient() {
  const [mode, setMode] = useState<"audit" | "analytics">("analytics");

  return (
    <div className="mx-auto w-full max-w-[1100px] px-5 py-6 sm:px-8">
      <div className="mb-4 flex gap-1 rounded-lg border border-black/10 bg-white p-0.5 w-fit">
        <button
          type="button"
          onClick={() => setMode("audit")}
          className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
            mode === "audit"
              ? "bg-[#ef5018] text-white"
              : "text-zo-text-secondary hover:bg-black/[0.03]"
          }`}
        >
          Audit log
        </button>
        <button
          type="button"
          onClick={() => setMode("analytics")}
          className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
            mode === "analytics"
              ? "bg-[#ef5018] text-white"
              : "text-zo-text-secondary hover:bg-black/[0.03]"
          }`}
        >
          Product analytics
        </button>
      </div>
      {mode === "audit" ? (
        <UserActivityPanel
          workspace="rfp"
          tone="rfp"
          title="RFP Intelligence — Activity"
          subtitle="Go/No-Go, proposals, knowledge base, and exports"
        />
      ) : (
        <UserAnalyticsPanel workspace="rfp" tone="rfp" className="min-h-[70vh]" />
      )}
    </div>
  );
}
