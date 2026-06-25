"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { computeOverallGoScore, daysUntil, formatCurrency, formatDate } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";
import { DeleteRfpButton } from "./DeleteRfpButton";
import { GoSign } from "./GoSign";
import { GoNoGoBadge, PriorityBadge, StatusBadge } from "./StatusBadge";
import { IconChevron } from "./ui/icons";
import { OutlineTabs } from "./ui/OutlineTabs";

type FilterTab = "all" | "go" | "pending" | "in_progress" | "urgent" | "new";

const filterTabs: { id: FilterTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "go", label: "Go RFPs" },
  { id: "pending", label: "Pending Approval" },
  { id: "in_progress", label: "In Progress" },
  { id: "urgent", label: "Urgent" },
  { id: "new", label: "New Intake" },
];

function filterRfps(rfps: RfpRecord[], tab: FilterTab): RfpRecord[] {
  switch (tab) {
    case "go":
      return rfps.filter((r) => r.goNoGo === "go");
    case "pending":
      return rfps.filter((r) => r.status === "pending_approval");
    case "in_progress":
      return rfps.filter((r) =>
        ["in_progress", "active", "review"].includes(r.status)
      );
    case "urgent":
      return rfps.filter(
        (r) =>
          r.priority === "critical" ||
          daysUntil(r.dueDate).urgent
      );
    case "new":
      return rfps.filter((r) => r.status === "new");
    default:
      return rfps;
  }
}

interface RfpTableProps {
  rfps: RfpRecord[];
  showFilters?: boolean;
}

export function RfpTable({ rfps, showFilters = true }: RfpTableProps) {
  const [activeFilter, setActiveFilter] = useState<FilterTab>("all");

  const filtered = useMemo(
    () => filterRfps(rfps, activeFilter),
    [rfps, activeFilter]
  );

  const tabsWithCounts = filterTabs.map((tab) => ({
    ...tab,
    count: filterRfps(rfps, tab.id).length,
  }));

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-6 border-b border-zo-border px-8 py-7">
        <div>
          <h2 className="font-heading text-xl font-bold text-foreground">
            Active RFPs
          </h2>
          <p className="mt-1.5 text-sm text-zo-text-muted">
            Fetched via JustWin · {rfps.length} opportunities in pipeline
          </p>
        </div>
      </div>

      {showFilters && (
        <div className="border-b border-zo-border px-8 py-5">
          <OutlineTabs
            tabs={tabsWithCounts}
            activeTab={activeFilter}
            onChange={(id) => setActiveFilter(id as FilterTab)}
          />
        </div>
      )}

      <div className="custom-scrollbar overflow-x-auto">
        <table className="w-full min-w-[1080px] text-left">
          <thead>
            <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-[11px] font-bold uppercase tracking-[0.12em] text-zo-text-secondary">
              <th className="px-8 py-4">RFP</th>
              <th className="px-5 py-4">Client</th>
              <th className="px-5 py-4">Stage</th>
              <th className="px-5 py-4">Due</th>
              <th className="px-5 py-4">Go Score</th>
              <th className="px-5 py-4">Go/No-Go</th>
              <th className="px-5 py-4">Status</th>
              <th className="px-5 py-4">Value</th>
              <th className="px-5 py-4" />
            </tr>
          </thead>
          <tbody className="divide-y divide-zo-border">
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={9}
                  className="px-8 py-16 text-center text-sm text-zo-text-muted"
                >
                  No RFPs match this filter.
                </td>
              </tr>
            ) : (
              filtered.map((rfp) => {
                const due = daysUntil(rfp.dueDate);
                const goScore = computeOverallGoScore(
                  rfp.fitScore,
                  rfp.worthScore,
                  rfp.goNoGoAnalysis?.decisionMatrix
                );
                const scale5 = goScore !== null && goScore <= 5;
                return (
                  <tr
                    key={rfp.id}
                    className="group border-b border-zo-border/60 transition-colors duration-150 hover:bg-[var(--zo-hover-bg)]"
                  >
                    <td className="px-8 py-6">
                      <div className="flex items-start gap-3">
                        {rfp.goNoGo === "go" && <GoSign className="mt-1" />}
                        <div className="min-w-0">
                          <Link
                            href={`/rfps/${rfp.id}`}
                            className="block max-w-sm font-semibold leading-snug text-foreground transition-colors group-hover:text-zo-orange"
                          >
                            {rfp.title}
                          </Link>
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <PriorityBadge priority={rfp.priority} />
                            {rfp.source === "manual" && (
                              <span className="rounded-md border border-zo-teal/40 bg-zo-teal/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-zo-teal">
                                Manual
                              </span>
                            )}
                            <span className="text-xs text-zo-text-muted">
                              {rfp.sector} · {rfp.location}
                            </span>
                          </div>
                          {rfp.pdfUrl && (
                            <a
                              href={rfp.pdfUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold text-zo-teal hover:text-zo-orange"
                            >
                              View PDF →
                            </a>
                          )}
                          {rfp.assignedTo && (
                            <p className="mt-1.5 text-xs text-zo-text-muted">
                              Assigned · {rfp.assignedTo}
                            </p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-6">
                      <p className="font-medium text-zo-text-secondary">
                        {rfp.client}
                      </p>
                      {rfp.externalId && (
                        <p className="mt-1 font-mono text-[11px] text-zo-text-muted">
                          {rfp.externalId}
                        </p>
                      )}
                    </td>
                    <td className="px-5 py-6">
                      <span className="text-sm font-semibold text-foreground">
                        {STAGE_LABELS[rfp.stage]}
                      </span>
                    </td>
                    <td className="px-5 py-6">
                      <p className="text-sm font-medium">
                        {formatDate(rfp.dueDate)}
                      </p>
                      <p
                        className={`mt-1 text-xs font-semibold ${
                          due.urgent ? "text-zo-error" : "text-zo-text-muted"
                        }`}
                      >
                        {due.label}
                      </p>
                    </td>
                    <td className="px-5 py-6">
                      {goScore !== null ? (
                        <span
                          className={`font-heading text-lg font-bold ${
                            scale5
                              ? goScore >= 4
                                ? "text-zo-success"
                                : goScore >= 3
                                  ? "text-zo-orange"
                                  : "text-zo-error"
                              : goScore >= 85
                                ? "text-zo-success"
                                : goScore >= 70
                                  ? "text-zo-orange"
                                  : "text-zo-error"
                          }`}
                        >
                          {scale5 ? `${goScore}/5` : goScore}
                        </span>
                      ) : (
                        <span className="text-zo-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-5 py-6">
                      {rfp.goNoGo === "go" ? (
                        <GoSign />
                      ) : (
                        <GoNoGoBadge recommendation={rfp.goNoGo} />
                      )}
                    </td>
                    <td className="px-5 py-6">
                      <StatusBadge status={rfp.status} />
                    </td>
                    <td className="px-5 py-6">
                      <p className="font-heading text-sm font-bold text-foreground">
                        {formatCurrency(rfp.estimatedValue)}
                      </p>
                    </td>
                    <td className="px-5 py-6">
                      <div className="flex flex-col items-end gap-2">
                        <Link
                          href={`/rfps/${rfp.id}`}
                          className="inline-flex"
                          aria-label={`Open ${rfp.title}`}
                        >
                          <IconChevron className="h-4 w-4 text-zo-text-muted opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100" />
                        </Link>
                        <DeleteRfpButton
                          rfpId={rfp.id}
                          title={rfp.title}
                          variant="table"
                        />
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
