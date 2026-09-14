"use client";

import { useEffect, useState } from "react";

type BudgetSnapshot = {
  enabled: boolean;
  limitUsd: number;
  spentUsd: number;
  remainingUsd: number;
  blocked: boolean;
  proposalSpentUsd: number;
  financialSpentUsd: number;
  weekSpentUsd: number;
  weekProposalSpentUsd: number;
  weekFinancialSpentUsd: number;
};

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function useAiBudget(): BudgetSnapshot | null {
  const [budget, setBudget] = useState<BudgetSnapshot | null>(null);

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
          proposalSpentUsd: Number(data.proposal_spent_usd ?? 0),
          financialSpentUsd: Number(data.financial_spent_usd ?? 0),
          weekSpentUsd: Number(data.week_spent_usd ?? 0),
          weekProposalSpentUsd: Number(data.week_proposal_spent_usd ?? 0),
          weekFinancialSpentUsd: Number(data.week_financial_spent_usd ?? 0),
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

  return budget;
}

/**
 * Left-sidebar monthly + weekly AI spend (Proposals / Finance split).
 * Per-person breakdown lives on Analytics.
 */
export function SidebarAiCostPanel({ collapsed }: { collapsed: boolean }) {
  const budget = useAiBudget();
  if (!budget?.enabled || budget.limitUsd <= 0) return null;

  const monthPct = Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100));

  if (collapsed) {
    return (
      <div
        className="mx-2 mb-4 rounded-xl border px-2 py-3 text-center"
        style={{
          borderColor: budget.blocked
            ? "color-mix(in srgb, var(--zo-orange) 55%, var(--zo-border))"
            : "var(--shell-border, var(--zo-border))",
        }}
        title={`Month ${fmtUsd(budget.spentUsd)} / ${fmtUsd(budget.limitUsd)} · Week ${fmtUsd(budget.weekSpentUsd)}`}
      >
        <p className="text-[10px] font-bold uppercase tracking-wider text-zo-text-muted">AI</p>
        <p className="mt-1 text-xs font-semibold tabular-nums text-foreground">
          {fmtUsd(budget.spentUsd)}
        </p>
        <p className="mt-0.5 text-[10px] tabular-nums text-zo-text-muted">
          w {fmtUsd(budget.weekSpentUsd)}
        </p>
      </div>
    );
  }

  return (
    <div
      className="mx-3 mb-4 rounded-2xl border px-3.5 py-3.5"
      style={{
        borderColor: budget.blocked
          ? "color-mix(in srgb, var(--zo-orange) 55%, var(--zo-border))"
          : "var(--shell-border, var(--zo-border))",
      }}
      aria-label={`Monthly AI ${fmtUsd(budget.spentUsd)} of ${fmtUsd(budget.limitUsd)}. Weekly ${fmtUsd(budget.weekSpentUsd)}.`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-zo-text-muted">
          AI cost
        </span>
        {budget.blocked ? (
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--zo-orange)]">
            Capped
          </span>
        ) : null}
      </div>

      <div className="mt-3 space-y-3">
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-xs font-medium text-zo-text-secondary">This month</span>
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {fmtUsd(budget.spentUsd)}
              <span className="text-xs font-medium text-zo-text-secondary">
                {" "}
                / {fmtUsd(budget.limitUsd)}
              </span>
            </span>
          </div>
          <div
            className="mt-1.5 h-1.5 overflow-hidden rounded-full"
            style={{ background: "var(--zo-surface-secondary, #eceae4)" }}
            aria-hidden
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${monthPct}%`,
                background: budget.blocked ? "var(--zo-orange)" : "var(--zo-teal, #0d9488)",
              }}
            />
          </div>
          <div className="mt-1.5 flex justify-between gap-2 text-[11px] tabular-nums text-zo-text-muted">
            <span>
              Prop <span className="text-foreground">{fmtUsd(budget.proposalSpentUsd)}</span>
            </span>
            <span>
              Fin <span className="text-foreground">{fmtUsd(budget.financialSpentUsd)}</span>
            </span>
          </div>
        </div>

        <div className="border-t border-[var(--shell-border,var(--zo-border))] pt-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-xs font-medium text-zo-text-secondary">This week</span>
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {fmtUsd(budget.weekSpentUsd)}
            </span>
          </div>
          <div className="mt-1.5 flex justify-between gap-2 text-[11px] tabular-nums text-zo-text-muted">
            <span>
              Prop <span className="text-foreground">{fmtUsd(budget.weekProposalSpentUsd)}</span>
            </span>
            <span>
              Fin <span className="text-foreground">{fmtUsd(budget.weekFinancialSpentUsd)}</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
