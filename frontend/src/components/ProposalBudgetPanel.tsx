"use client";

import type { ProposalBudget } from "@/types/proposal";
import { FeeJustificationBlock } from "./ProposalReviewPanel";

function formatUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

interface ProposalBudgetPanelProps {
  budget: ProposalBudget | null;
  isRunning: boolean;
  error: string | null;
  disabled?: boolean;
  onGenerate: () => void;
}

export function ProposalBudgetPanel({
  budget,
  isRunning,
  error,
  disabled,
  onGenerate,
}: ProposalBudgetPanelProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-heading text-lg font-bold text-foreground md:text-xl">
            Budget & Pricing
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-zo-text-muted">
            Stage 3 budget: Stage 1 tier + Stage 2 scope + 00_Guide_Pricing from
            Supermemory. Run Go/No-Go and generate proposal (Phase 2) first for best results.
          </p>
        </div>
        <button
          type="button"
          onClick={onGenerate}
          disabled={disabled || isRunning}
          className="zo-btn !py-2 disabled:opacity-50"
        >
          {isRunning
            ? "Building budget…"
            : budget
              ? "Regenerate budget"
              : "Generate budget"}
        </button>
      </div>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-zo-error">
          {error}
        </p>
      )}

      {!budget && !isRunning && (
        <div className="rounded-xl border border-dashed border-zo-border bg-[#fafbfc] px-4 py-8 text-center">
          <p className="text-sm text-zo-text-muted">
            Run Go/No-Go (Stage 1) and generate proposal for scope map (Stage 2),
            then build budget from 00_Guide_Pricing + Supermemory.
          </p>
        </div>
      )}

      {budget && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                RFP budget cap
              </p>
              <p className="font-heading mt-1.5 text-2xl font-bold tabular-nums">
                {formatUsd(budget.rfpBudgetCap)}
              </p>
            </div>
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Agency revenue est.
              </p>
              <p className="font-heading mt-1.5 text-2xl font-bold tabular-nums">
                {formatUsd(budget.agencyRevenueEstimate)}
              </p>
            </div>
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Pricing tier
              </p>
              <p className="mt-1.5 text-sm font-medium capitalize text-foreground">
                {budget.pricingTier ?? "—"}
              </p>
            </div>
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Budget format
              </p>
              <p className="mt-1.5 text-sm font-medium capitalize text-foreground">
                {budget.budgetFormat?.replace(/_/g, " ") ?? "—"}
              </p>
            </div>
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Fee structure
              </p>
              <p className="mt-1.5 text-sm font-medium text-foreground">
                {budget.feeStructure || "—"}
              </p>
            </div>
            <div className="proposal-stat-card">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Confidence
              </p>
              <p className="font-heading mt-1.5 text-2xl font-bold tabular-nums">
                {budget.confidence}%
              </p>
            </div>
          </div>

          {budget.confidence < 50 && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
              <p className="text-sm font-medium text-red-900">
                Low confidence ({budget.confidence}%) — not submission-ready. Sonja
                must approve rates and assign named team members before use.
              </p>
            </div>
          )}

          {budget.verifiedRates.length > 0 && (
            <div>
              <h3 className="text-sm font-bold text-foreground">
                Verified rates (from KB)
              </h3>
              <div className="mt-2 overflow-x-auto rounded-xl border border-zo-border">
                <table className="min-w-full text-left text-sm">
                  <thead className="border-b border-zo-border bg-[#fafbfc] text-[10px] font-bold uppercase tracking-wider text-zo-text-muted">
                    <tr>
                      <th className="px-4 py-2">Person</th>
                      <th className="px-4 py-2">Role</th>
                      <th className="px-4 py-2">Rate/hr</th>
                      <th className="px-4 py-2">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {budget.verifiedRates.map((row) => (
                      <tr key={`${row.personName}-${row.source}`} className="border-b border-zo-border/60">
                        <td className="px-4 py-2">{row.personName}</td>
                        <td className="px-4 py-2 text-zo-text-secondary">{row.role}</td>
                        <td className="px-4 py-2 tabular-nums">{formatUsd(row.hourlyRate)}</td>
                        <td className="px-4 py-2 text-[11px] text-zo-text-muted">{row.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {budget.mediaSpendNotes && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Media spend</h3>
              <p className="mt-2 text-sm text-zo-text-secondary">{budget.mediaSpendNotes}</p>
            </div>
          )}

          {budget.optionTermNotes && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Option terms (Years 2+)</h3>
              <p className="mt-2 text-sm text-zo-text-secondary">{budget.optionTermNotes}</p>
            </div>
          )}

          {budget.pricingFlags.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-800">
                Pricing flags
              </p>
              <ul className="mt-2 space-y-1 text-sm text-amber-900">
                {budget.pricingFlags.map((flag) => (
                  <li key={flag}>{flag}</li>
                ))}
              </ul>
            </div>
          )}

          {budget.tiers.length > 0 && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Tiers</h3>
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                {budget.tiers.map((tier) => (
                  <div
                    key={tier.id}
                    className={`rounded-xl border px-4 py-3 ${
                      tier.id === budget.recommendedTierId
                        ? "border-zo-orange bg-[#fff8f5]"
                        : "border-zo-border bg-white"
                    }`}
                  >
                    <p className="font-medium text-foreground">
                      {tier.name}
                      {tier.id === budget.recommendedTierId && (
                        <span className="ml-2 text-[10px] font-bold uppercase text-zo-orange">
                          Recommended
                        </span>
                      )}
                    </p>
                    <p className="font-heading mt-1 text-lg font-bold tabular-nums">
                      {formatUsd(tier.total)}
                    </p>
                    <p className="mt-2 text-xs text-zo-text-muted">{tier.rationale}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {budget.lineItems.length > 0 && (
            <div className="overflow-x-auto rounded-xl border border-zo-border">
              <table className="min-w-full text-left text-sm">
                <thead className="border-b border-zo-border bg-[#fafbfc] text-[10px] font-bold uppercase tracking-wider text-zo-text-muted">
                  <tr>
                    <th className="px-4 py-3">Category</th>
                    <th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3">Qty</th>
                    <th className="px-4 py-3">Rate</th>
                    <th className="px-4 py-3">Extended</th>
                  </tr>
                </thead>
                <tbody>
                  {budget.lineItems.map((item) => (
                    <tr key={item.id} className="border-b border-zo-border/60">
                      <td className="px-4 py-3 text-zo-text-secondary">{item.category}</td>
                      <td className="px-4 py-3">
                        <p>
                          {item.namedPerson
                            ? `${item.roleTitle || item.description} — ${item.namedPerson}`
                            : item.description}
                        </p>
                        {item.rateSource && (
                          <p className="mt-1 text-[11px] text-zo-text-muted">
                            {item.rateSource}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {item.quantity ?? "—"} {item.unit}
                      </td>
                      <td className="px-4 py-3 tabular-nums">{formatUsd(item.rate)}</td>
                      <td className="px-4 py-3 tabular-nums font-medium">
                        {formatUsd(item.extended)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {budget.scopeAdjustments.length > 0 && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Scope adjustments</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zo-text-secondary">
                {budget.scopeAdjustments.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {budget.qualifyingLanguage && (
            <div>
              <h3 className="text-sm font-bold text-foreground">
                Pricing narrative (for proposal)
              </h3>
              <div className="mt-2 whitespace-pre-wrap rounded-xl border border-zo-border bg-white px-4 py-3 text-sm leading-relaxed text-zo-text-secondary">
                {budget.qualifyingLanguage}
              </div>
            </div>
          )}

          {budget.feeJustificationMemo?.markdown && (
            <FeeJustificationBlock markdown={budget.feeJustificationMemo.markdown} />
          )}

          {budget.scopeSummary && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Scope summary</h3>
              <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                {budget.scopeSummary}
              </p>
            </div>
          )}

          {budget.designBrief && (
            <div>
              <h3 className="text-sm font-bold text-foreground">Design brief</h3>
              <div className="mt-2 whitespace-pre-wrap rounded-xl border border-zo-border bg-[#fafbfc] px-4 py-3 text-sm leading-relaxed text-zo-text-secondary">
                {budget.designBrief}
              </div>
            </div>
          )}

          {budget.kbBucketsUsed.length > 0 && (
            <p className="text-[11px] text-zo-text-muted">
              KB buckets: {budget.kbBucketsUsed.join(" · ")}
            </p>
          )}

          {budget.kbSources.length > 0 && (
            <p className="text-[11px] text-zo-text-muted">
              KB sources: {budget.kbSources.slice(0, 8).join(" · ")}
            </p>
          )}
        </>
      )}
    </div>
  );
}
