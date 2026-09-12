import Link from "next/link";
import type { LlmCostSummary } from "@/lib/llm-cost-service";

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(n);
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function LlmCostPanel({ summary }: { summary: LlmCostSummary }) {
  const attributed = summary.byProposal.filter((p) => p.rfpId !== "unknown");
  const topStages = summary.byNode.slice(0, 12);
  const unknownModels = summary.unknownBreakdown.byModel.slice(0, 8);
  const unknownDates = summary.unknownBreakdown.byDate.slice(0, 8);
  const budget = summary.monthlyBudget;
  const usedPct =
    budget && budget.enabled && budget.limitUsd > 0
      ? Math.min(100, Math.round((budget.spentUsd / budget.limitUsd) * 100))
      : 0;

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

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <div className="zo-card p-6">
          <p className="text-sm text-zo-text-secondary">Total LLM spend</p>
          <p className="mt-2 font-heading text-3xl font-bold">{fmtUsd(summary.totalCostUsd)}</p>
          <p className="mt-1 text-sm text-zo-text-secondary">
            {summary.callCount.toLocaleString()} API calls
          </p>
        </div>
        <div className="zo-card p-6">
          <p className="text-sm text-zo-text-secondary">Attributed to proposals</p>
          <p className="mt-2 font-heading text-3xl font-bold">
            {fmtUsd(summary.totalCostUsd - summary.unattributedCostUsd)}
          </p>
          <p className="mt-1 text-sm text-zo-text-secondary">
            {summary.proposalCount} proposals tracked
          </p>
        </div>
        <div className="zo-card p-6">
          <p className="text-sm text-zo-text-secondary">Unattributed</p>
          <p className="mt-2 font-heading text-3xl font-bold">
            {fmtUsd(summary.unattributedCostUsd)}
          </p>
          <p className="mt-1 text-sm text-zo-text-secondary">
            Early runs before per-RFP tagging
          </p>
        </div>
        <div className="zo-card p-6">
          <p className="text-sm text-zo-text-secondary">Tokens (in / out)</p>
          <p className="mt-2 font-heading text-2xl font-bold">
            {fmtTokens(summary.totalInputTokens)} / {fmtTokens(summary.totalOutputTokens)}
          </p>
          <p className="mt-1 text-sm text-zo-text-secondary">Estimated from provider usage</p>
        </div>
      </div>

      {summary.unknownNodeCalls > 0 ? (
        <div className="zo-card overflow-hidden">
          <div className="border-b border-zo-border px-6 py-4">
            <h2 className="font-heading text-xl font-bold">Unknown stage calls</h2>
            <p className="mt-1 text-sm text-zo-text-secondary">
              {fmtUsd(summary.unknownNodeCostUsd)} across {summary.unknownNodeCalls.toLocaleString()}{" "}
              calls missing a pipeline stage tag — usually older runs or helper paths without{" "}
              <code className="text-xs">node_name</code>.
            </p>
          </div>
          <div className="grid gap-0 lg:grid-cols-2">
            <div className="overflow-x-auto border-b border-zo-border lg:border-b-0 lg:border-r">
              <table className="w-full text-sm">
                <thead className="bg-zo-surface-secondary text-left text-zo-text-secondary">
                  <tr>
                    <th className="px-6 py-3 font-medium">Model</th>
                    <th className="px-4 py-3 font-medium text-right">Cost</th>
                    <th className="px-4 py-3 font-medium text-right">Calls</th>
                  </tr>
                </thead>
                <tbody>
                  {unknownModels.map((row) => (
                    <tr key={row.model ?? "unknown"} className="border-t border-zo-border">
                      <td className="px-6 py-3 font-mono text-xs">{row.model ?? "unknown"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{fmtUsd(row.costUsd)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{row.calls}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-zo-surface-secondary text-left text-zo-text-secondary">
                  <tr>
                    <th className="px-6 py-3 font-medium">Date</th>
                    <th className="px-4 py-3 font-medium text-right">Cost</th>
                    <th className="px-4 py-3 font-medium text-right">Calls</th>
                  </tr>
                </thead>
                <tbody>
                  {unknownDates.map((row) => (
                    <tr key={row.date ?? "unknown"} className="border-t border-zo-border">
                      <td className="px-6 py-3 font-mono text-xs">{row.date ?? "unknown"}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{fmtUsd(row.costUsd)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{row.calls}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-2">
        <div className="zo-card overflow-hidden">
          <div className="border-b border-zo-border px-6 py-4">
            <h2 className="font-heading text-xl font-bold">Cost per proposal</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-zo-surface-secondary text-left text-zo-text-secondary">
                <tr>
                  <th className="px-6 py-3 font-medium">Proposal</th>
                  <th className="px-4 py-3 font-medium text-right">Cost</th>
                  <th className="px-4 py-3 font-medium text-right">Calls</th>
                  <th className="px-4 py-3 font-medium text-right">Runs</th>
                </tr>
              </thead>
              <tbody>
                {attributed.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-8 text-zo-text-secondary">
                      No per-proposal costs recorded yet.
                    </td>
                  </tr>
                ) : (
                  attributed.map((row) => (
                    <tr key={row.rfpId} className="border-t border-zo-border">
                      <td className="px-6 py-3">
                        <Link
                          href={`/rfps/${encodeURIComponent(row.rfpId)}`}
                          className="font-medium hover:underline"
                        >
                          {row.title || row.rfpId}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">{fmtUsd(row.costUsd)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{row.calls}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{row.runCount}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="zo-card overflow-hidden">
          <div className="border-b border-zo-border px-6 py-4">
            <h2 className="font-heading text-xl font-bold">Cost by pipeline stage</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-zo-surface-secondary text-left text-zo-text-secondary">
                <tr>
                  <th className="px-6 py-3 font-medium">Stage</th>
                  <th className="px-4 py-3 font-medium text-right">Cost</th>
                  <th className="px-4 py-3 font-medium text-right">Calls</th>
                </tr>
              </thead>
              <tbody>
                {topStages.map((row) => (
                  <tr key={row.nodeName} className="border-t border-zo-border">
                    <td className="px-6 py-3 font-mono text-xs">{row.nodeName}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtUsd(row.costUsd)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{row.calls}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
