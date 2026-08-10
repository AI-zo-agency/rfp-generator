"use client";

import Link from "next/link";
import { formatRelativeTime } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/rfp-process";
import type { CurrentProposalItem } from "@/types/rfp";

interface CurrentProposalsPanelProps {
  latest: CurrentProposalItem | null;
  proposals: CurrentProposalItem[];
}

function progressPct(filled: number, total: number) {
  if (total <= 0) return 0;
  return Math.min(100, Math.round((filled / total) * 100));
}

export function CurrentProposalsPanel({
  latest,
  proposals,
}: CurrentProposalsPanelProps) {
  if (!latest && proposals.length === 0) {
    return (
      <section className="zo-card p-6 sm:p-7">
        <h2 className="font-heading text-lg font-semibold text-foreground">
          Current Proposals
        </h2>
        <p className="mt-2 max-w-md text-sm leading-relaxed text-zo-text-muted">
          No drafts yet. Mark an RFP as Go, then open Proposals to generate.
        </p>
        <Link href="/proposals" className="zo-btn mt-5 inline-flex !py-2.5">
          Open Proposals
        </Link>
      </section>
    );
  }

  const ordered = latest
    ? [latest, ...proposals.filter((p) => p.rfpId !== latest.rfpId)]
    : proposals;

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex items-baseline justify-between gap-4 px-6 pt-5 sm:px-7 sm:pt-6">
        <h2 className="font-heading text-lg font-semibold text-foreground">
          Current Proposals
        </h2>
        <Link
          href="/proposals"
          className="text-sm font-medium text-zo-text-secondary transition-colors hover:text-zo-orange"
        >
          Workspace
        </Link>
      </div>

      <ul className="mt-4">
        {ordered.map((proposal, index) => {
          const href = `/proposals?rfp=${encodeURIComponent(proposal.rfpId)}`;
          const isLatest = index === 0;
          const pct = progressPct(proposal.filledCount, proposal.sectionCount);

          return (
            <li
              key={proposal.rfpId}
              className={
                isLatest
                  ? "border-t border-zo-border bg-[rgba(239,80,24,0.03)]"
                  : "border-t border-zo-border/70"
              }
            >
              <Link
                href={href}
                className="group flex items-center gap-4 px-6 py-4 transition-colors hover:bg-[var(--zo-hover-bg)] sm:px-7"
              >
                <div className="min-w-0 flex-1">
                  <p
                    className={`truncate font-semibold text-foreground group-hover:text-zo-orange ${
                      isLatest ? "text-base" : "text-sm"
                    }`}
                  >
                    {proposal.rfpTitle}
                  </p>
                  <p className="mt-1 truncate text-xs text-zo-text-muted">
                    {proposal.client}
                    {proposal.client ? " · " : ""}
                    {STAGE_LABELS[proposal.stage] ?? proposal.stage}
                    {" · "}
                    {formatRelativeTime(proposal.updatedAt)}
                  </p>
                  <div className="mt-2.5 flex items-center gap-3">
                    <div
                      className="h-1 max-w-[9rem] flex-1 overflow-hidden rounded-full bg-black/[0.06]"
                      aria-hidden
                    >
                      <div
                        className="h-full rounded-full bg-[#ef5018]"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="tabular-nums text-[11px] font-medium text-zo-text-secondary">
                      {proposal.filledCount}/{proposal.sectionCount}
                    </span>
                  </div>
                </div>
                {isLatest ? (
                  <span className="zo-btn shrink-0 !px-3.5 !py-2 text-xs">
                    Open
                  </span>
                ) : (
                  <span
                    className="shrink-0 text-zo-text-muted transition-colors group-hover:text-zo-orange"
                    aria-hidden
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                    </svg>
                  </span>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
