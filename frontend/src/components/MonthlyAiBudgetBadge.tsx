"use client";

import { useMonthlyAiBudget } from "@/lib/use-monthly-ai-budget";

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

/**
 * Navbar spend meter: total vs cap + Proposals / Finance split.
 * Sized to match header controls — readable at a glance.
 */
export function MonthlyAiBudgetBadge() {
  const budget = useMonthlyAiBudget();

  if (!budget?.enabled || budget.limitUsd <= 0) return null;

  const usedPct = Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100));

  return (
    <div
      className="hidden items-center gap-3 rounded-2xl border px-4 py-2.5 md:inline-flex"
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
      aria-label={`AI ${fmtUsd(budget.spentUsd)} of ${fmtUsd(budget.limitUsd)}. Proposals ${fmtUsd(budget.proposalSpentUsd)}. Finance ${fmtUsd(budget.financialSpentUsd)}.`}
    >
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-zo-text-secondary">
            AI
          </span>
          <span className="text-base font-semibold tabular-nums leading-none text-foreground">
            {fmtUsd(budget.spentUsd)}
            <span className="text-sm font-medium text-zo-text-secondary">
              {" "}
              / {fmtUsd(budget.limitUsd)}
            </span>
          </span>
        </div>
        <span
          className="h-2 w-full min-w-[7.5rem] overflow-hidden rounded-full"
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
      </div>

      <span className="h-8 w-px shrink-0 bg-[var(--zo-border,#e5e2da)]" aria-hidden />

      <div className="flex flex-col gap-0.5 text-sm leading-tight">
        <span className="tabular-nums text-zo-text-secondary">
          Proposals{" "}
          <span className="font-semibold text-foreground">
            {fmtUsd(budget.proposalSpentUsd)}
          </span>
        </span>
        <span className="tabular-nums text-zo-text-secondary">
          Finance{" "}
          <span className="font-semibold text-foreground">
            {fmtUsd(budget.financialSpentUsd)}
          </span>
        </span>
      </div>
    </div>
  );
}
