"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type MonthlyBudget = {
  enabled: boolean;
  limitUsd: number;
  spentUsd: number;
  remainingUsd: number;
  blocked: boolean;
};

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

/**
 * Compact spent / monthly cap for the top navbar.
 * Polls lightly so it stays current while generate/scan/chat run.
 */
export function MonthlyAiBudgetBadge() {
  const [budget, setBudget] = useState<MonthlyBudget | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch("/api/llm-cost/monthly-budget", {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return;
        const data = (await res.json()) as Record<string, unknown>;
        if (cancelled) return;
        if (!data || data.enabled === false) {
          setBudget(null);
          return;
        }
        setBudget({
          enabled: Boolean(data.enabled),
          limitUsd: Number(data.limit_usd ?? 0),
          spentUsd: Number(data.spent_usd ?? 0),
          remainingUsd: Number(data.remaining_usd ?? 0),
          blocked: Boolean(data.blocked),
        });
      } catch {
        /* keep last known */
      }
    }

    void load();
    const id = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (!budget?.enabled || budget.limitUsd <= 0) return null;

  const usedPct = Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100));

  return (
    <Link
      href="/analytics"
      className="hidden items-center gap-2.5 rounded-full border px-3 py-1.5 no-underline transition-colors hover:bg-black/[0.03] sm:inline-flex"
      style={{
        borderColor: budget.blocked
          ? "color-mix(in srgb, var(--zo-orange) 55%, var(--zo-border))"
          : "var(--zo-border)",
      }}
      title={
        budget.blocked
          ? "Monthly AI budget reached — all AI paused until next UTC month"
          : `${fmtUsd(budget.remainingUsd)} remaining this month`
      }
      aria-label={`AI spend ${fmtUsd(budget.spentUsd)} of ${fmtUsd(budget.limitUsd)} monthly limit`}
    >
      <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-zo-text-secondary">
        AI
      </span>
      <span className="text-xs font-semibold tabular-nums text-foreground">
        {fmtUsd(budget.spentUsd)}
        <span className="font-medium text-zo-text-secondary"> / {fmtUsd(budget.limitUsd)}</span>
      </span>
      <span
        className="h-1.5 w-14 overflow-hidden rounded-full"
        style={{ background: "var(--zo-surface-secondary, #eceae4)" }}
        aria-hidden
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${usedPct}%`,
            background: budget.blocked ? "var(--zo-orange)" : "var(--zo-teal, #0d9488)",
          }}
        />
      </span>
    </Link>
  );
}
