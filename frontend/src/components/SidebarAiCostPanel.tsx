"use client";

import { useEffect, useRef, useState } from "react";
import {
  useMonthlyAiBudget,
  useWeekCostHistory,
  type MonthlyAiBudgetSnapshot,
} from "@/lib/use-monthly-ai-budget";

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function ProductSplit({
  ralph,
  financial,
  outreach,
}: {
  ralph: number;
  financial: number;
  outreach: number;
}) {
  const rows = [
    ["Ralph", ralph],
    ["Financial", financial],
    ["Outreach", outreach],
  ] as const;
  return (
    <ul className="mt-1.5 space-y-1">
      {rows.map(([label, amount]) => (
        <li key={label} className="flex items-baseline justify-between gap-2 text-[11px] text-zo-text-muted">
          <span>{label}</span>
          <span className="tabular-nums text-foreground">{fmtUsd(amount)}</span>
        </li>
      ))}
    </ul>
  );
}

function Meter({ pct, hot }: { pct: number; hot: boolean }) {
  return (
    <div
      className="mt-1.5 h-1.5 overflow-hidden rounded-full"
      style={{ background: "var(--zo-surface-secondary, #eceae4)" }}
      aria-hidden
    >
      <div
        className="h-full rounded-full"
        style={{
          width: `${pct}%`,
          background: hot ? "var(--zo-orange)" : "var(--zo-teal, #0d9488)",
        }}
      />
    </div>
  );
}

function HistoryBlock({ budget }: { budget: MonthlyAiBudgetSnapshot }) {
  const history = useWeekCostHistory(true);
  const dayLimit = budget.dayLimitUsd > 0 ? budget.dayLimitUsd : 5;
  const dayPct = Math.min(100, Math.round((budget.daySpentUsd / dayLimit) * 100));
  const monthPct =
    budget.limitUsd > 0
      ? Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100))
      : 0;

  return (
    <div className="mt-3 space-y-3 border-t border-[var(--shell-border,var(--zo-border))] pt-3">
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium text-zo-text-secondary">Today</span>
          <span className="text-sm font-semibold tabular-nums text-foreground">
            {fmtUsd(budget.daySpentUsd)}
            <span className="text-xs font-medium text-zo-text-secondary"> / {fmtUsd(dayLimit)}</span>
          </span>
        </div>
        <Meter pct={dayPct} hot={budget.daySpentUsd >= dayLimit} />
        <ProductSplit
          ralph={budget.dayProposalSpentUsd}
          financial={budget.dayFinancialSpentUsd}
          outreach={budget.dayOutreachSpentUsd}
        />
      </div>
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium text-zo-text-secondary">This month</span>
          <span className="text-sm font-semibold tabular-nums text-foreground">
            {fmtUsd(budget.spentUsd)}
            <span className="text-xs font-medium text-zo-text-secondary"> / {fmtUsd(budget.limitUsd)}</span>
          </span>
        </div>
        <Meter pct={monthPct} hot={budget.blocked} />
        <ProductSplit
          ralph={budget.proposalSpentUsd}
          financial={budget.financialSpentUsd}
          outreach={budget.outreachSpentUsd}
        />
      </div>
      <div>
        <p className="text-xs font-medium text-zo-text-secondary">Earlier weeks</p>
        {history.loading ? (
          <p className="mt-1.5 text-[11px] text-zo-text-muted">Loading earlier weeks…</p>
        ) : history.error ? (
          <p className="mt-1.5 text-[11px] text-zo-text-muted">{history.error}</p>
        ) : history.weeks && history.weeks.length > 0 ? (
          <ul className="mt-1.5 space-y-2">
            {history.weeks.map((week) => (
              <li key={week.label}>
                <div className="flex items-baseline justify-between gap-2 text-[11px]">
                  <span className="text-zo-text-secondary">{week.label}</span>
                  <span className="font-semibold tabular-nums text-foreground">{fmtUsd(week.spentUsd)}</span>
                </div>
                <ProductSplit
                  ralph={week.proposalSpentUsd}
                  financial={week.financialSpentUsd}
                  outreach={week.outreachSpentUsd}
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-[11px] text-zo-text-muted">No earlier spend in the last 8 weeks.</p>
        )}
      </div>
    </div>
  );
}

function AiCostCard({
  budget,
  frame,
}: {
  budget: MonthlyAiBudgetSnapshot;
  frame: string;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);

  return (
    <div
      className={`${frame} max-h-[min(50vh,26rem)] overflow-y-auto rounded-2xl border px-3.5 py-3.5`}
      style={{
        borderColor: budget.blocked
          ? "color-mix(in srgb, var(--zo-orange) 55%, var(--zo-border))"
          : "var(--shell-border, var(--zo-border))",
      }}
      aria-label={`This week AI ${fmtUsd(budget.weekSpentUsd)}. Ralph ${fmtUsd(budget.weekProposalSpentUsd)}. Financial ${fmtUsd(budget.weekFinancialSpentUsd)}. Outreach ${fmtUsd(budget.weekOutreachSpentUsd)}.`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-zo-text-muted">
          AI cost
        </span>
        {budget.blocked ? (
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--zo-orange)]">
            Capped
          </span>
        ) : (
          <span className="text-[10px] font-semibold uppercase tracking-wide text-zo-text-muted">
            This week
          </span>
        )}
      </div>
      <p className="mt-2 text-lg font-semibold tabular-nums text-foreground">
        {fmtUsd(budget.weekSpentUsd)}
      </p>
      <ProductSplit
        ralph={budget.weekProposalSpentUsd}
        financial={budget.weekFinancialSpentUsd}
        outreach={budget.weekOutreachSpentUsd}
      />
      <button
        type="button"
        className="mt-3 text-[11px] font-semibold text-zo-text-secondary underline-offset-2 hover:underline"
        aria-expanded={historyOpen}
        onClick={() => setHistoryOpen((open) => !open)}
      >
        {historyOpen ? "Hide history" : "History"}
      </button>
      {historyOpen ? <HistoryBlock budget={budget} /> : null}
    </div>
  );
}

/**
 * Header chip. The full breakdown opens under the bar so the sticky row
 * stays one line on a phone. The header's backdrop-blur is the fixed
 * containing block, so `top-full` lands just below it.
 */
function AiCostMenu({ budget }: { budget: MonthlyAiBudgetSnapshot }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[#274742]/20 bg-white px-2.5 text-xs font-bold text-[#274742] transition hover:bg-[#edf3f1]"
        aria-expanded={open}
        aria-controls="ai-cost-menu"
        aria-label={`AI cost this week ${fmtUsd(budget.weekSpentUsd)}`}
        onClick={() => {
          setOpen((prev) => {
            const next = !prev;
            console.debug("[ai-cost] menu", { open: next });
            return next;
          });
        }}
      >
        <span className="text-[10px] font-bold uppercase tracking-wide text-[#52635f]">AI</span>
        <span className="tabular-nums">{fmtUsd(budget.weekSpentUsd)}</span>
        <span aria-hidden className={`text-[10px] leading-none transition ${open ? "rotate-180" : ""}`}>
          ▾
        </span>
      </button>
      {open ? (
        <div
          id="ai-cost-menu"
          className="fixed inset-x-4 top-full z-50 mt-2 sm:absolute sm:inset-x-auto sm:right-0 sm:w-64"
        >
          <AiCostCard budget={budget} frame="mx-0 mb-0 w-full bg-white shadow-[0_16px_40px_rgba(10,15,26,0.14)]" />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Sidebar AI spend. Open view is the current UTC week, split by product.
 * Today, this month, and prior weeks sit under History.
 * `menu` is the header placement: a spend chip, breakdown on demand.
 */
export function SidebarAiCostPanel({
  collapsed,
  className = "",
  variant = "card",
}: {
  collapsed: boolean;
  className?: string;
  variant?: "card" | "menu";
}) {
  const budget = useMonthlyAiBudget();
  if (!budget?.enabled || budget.limitUsd <= 0) return null;
  const frame = className || (collapsed ? "mx-2 mb-4" : "mx-3 mb-4");

  if (variant === "menu") return <AiCostMenu budget={budget} />;

  if (collapsed) {
    return (
      <div
        className={`${frame} rounded-xl border px-2 py-3 text-center`}
        style={{
          borderColor: budget.blocked
            ? "color-mix(in srgb, var(--zo-orange) 55%, var(--zo-border))"
            : "var(--shell-border, var(--zo-border))",
        }}
        title={`This week ${fmtUsd(budget.weekSpentUsd)} · Ralph ${fmtUsd(budget.weekProposalSpentUsd)} · Financial ${fmtUsd(budget.weekFinancialSpentUsd)} · Outreach ${fmtUsd(budget.weekOutreachSpentUsd)}`}
      >
        <p className="text-[10px] font-bold uppercase tracking-wider text-zo-text-muted">AI</p>
        <p className="mt-1 text-xs font-semibold tabular-nums text-foreground">
          {fmtUsd(budget.weekSpentUsd)}
        </p>
      </div>
    );
  }

  return <AiCostCard budget={budget} frame={frame} />;
}
