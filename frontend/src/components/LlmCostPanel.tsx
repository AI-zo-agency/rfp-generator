import type { LlmCostSummary } from "@/lib/llm-cost-service";

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(n);
}

// All-time rollup helpers kept for when we re-enable historical Analytics.
// function fmtTokens(n: number): string {
//   if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
//   if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
//   return String(n);
// }

export function LlmCostPanel({ summary }: { summary: LlmCostSummary }) {
  // Temporarily hide all-time Analytics (total spend / proposals / stages / unknown).
  // const attributed = summary.byProposal.filter((p) => p.rfpId !== "unknown");
  // const topStages = summary.byNode.slice(0, 12);
  // const unknownModels = summary.unknownBreakdown.byModel.slice(0, 8);
  // const unknownDates = summary.unknownBreakdown.byDate.slice(0, 8);
  const budget = summary.monthlyBudget;
  const usedPct =
    budget && budget.enabled && budget.limitUsd > 0
      ? Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100))
      : 0;
  const byPerson = (budget?.proposalByUser ?? []).filter((row) =>
    row.email.includes("@")
  );

  return (
    <div className="space-y-8">
      {budget?.enabled ? (
        <div
          className={`zo-card p-6 ${
            budget.blocked ? "border border-zo-orange" : ""
          }`}
        >
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-sm text-zo-text-secondary">
                Monthly AI budget ({budget.timezone})
              </p>
              <p className="mt-2 font-heading text-3xl font-bold">
                {fmtUsd(budget.spentUsd)}
                <span className="ml-2 text-lg font-medium text-zo-text-secondary">
                  / {fmtUsd(budget.limitUsd)}
                </span>
              </p>
              <p className="mt-1 text-sm text-zo-text-secondary">
                {budget.blocked
                  ? "Hard capped — all AI features paused until next UTC month"
                  : `${fmtUsd(budget.remainingUsd)} remaining this month`}
              </p>
            </div>
            <div className="text-right text-sm text-zo-text-secondary">
              <p>Proposals · {fmtUsd(budget.proposalSpentUsd)}</p>
              <p>Finance · {fmtUsd(budget.financialSpentUsd)}</p>
              <p className="mt-1 text-xs">
                Counts spend after deploy epoch · resets {budget.periodEnd.slice(0, 10)}
              </p>
            </div>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-zo-surface-secondary">
            <div
              className="h-full"
              style={{
                width: `${usedPct}%`,
                background: budget.blocked
                  ? "var(--zo-orange)"
                  : "var(--zo-teal)",
              }}
            />
          </div>
        </div>
      ) : null}

      <div className="zo-card overflow-hidden">
        <div className="border-b border-zo-border px-6 py-4">
          <h2 className="font-heading text-xl font-bold">Cost per person</h2>
          <p className="mt-1 text-sm text-zo-text-secondary">
            Proposal AI spend this month by signed-in email (from when attribution
            started). Older runs without an email are not listed here.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zo-surface-secondary text-left text-zo-text-secondary">
              <tr>
                <th className="px-6 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium text-right">Proposal cost</th>
              </tr>
            </thead>
            <tbody>
              {byPerson.length === 0 ? (
                <tr>
                  <td colSpan={2} className="px-6 py-8 text-zo-text-secondary">
                    No attributed user spend yet — run Generate, Scan, Go/No-Go, or
                    section chat while signed in.
                  </td>
                </tr>
              ) : (
                byPerson.map((row) => (
                  <tr key={row.email} className="border-t border-zo-border">
                    <td className="px-6 py-3">{row.email}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {fmtUsd(row.proposalSpentUsd)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/*
      === All-time LLM Analytics (temporarily disabled) ===
      Total / Attributed / Unattributed / Tokens cards,
      Unknown stage calls, Cost per proposal, Cost by pipeline stage.
      Re-enable with GET /llm-cost/summary when needed.

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        ... totalCostUsd / unattributedCostUsd / tokens ...
      </div>
      {summary.unknownNodeCalls > 0 ? ( ... unknown breakdown ... ) : null}
      <div className="grid gap-8 lg:grid-cols-2">
        ... Cost per proposal / Cost by pipeline stage ...
      </div>
      */}
    </div>
  );
}
