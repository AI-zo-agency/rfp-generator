"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { motion } from "motion/react";
import { computeOverallGoScore, daysUntil, formatDate } from "@/lib/format";
import { expoOutEase } from "@/lib/motion";
import {
  isNewIntake,
  isProposalInProgress,
  needsGoNoGoDecision,
  STAGE_LABELS,
} from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";
import { DeleteRfpButton } from "./DeleteRfpButton";
import { GoSign } from "./GoSign";
import { GoNoGoBadge, PriorityBadge } from "./StatusBadge";
import { IconChevron } from "./ui/icons";
import { OutlineTabs } from "./ui/OutlineTabs";

type FilterTab = "all" | "go" | "pending" | "in_progress" | "new";

const filterTabs: { id: FilterTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "go", label: "Go RFPs" },
  { id: "pending", label: "Needs Decision" },
  { id: "in_progress", label: "Drafting" },
  { id: "new", label: "New Intake" },
];

const rowVariants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.32, ease: expoOutEase },
  },
};

function filterRfps(rfps: RfpRecord[], tab: FilterTab): RfpRecord[] {
  switch (tab) {
    case "go":
      return rfps.filter((r) => r.goNoGo === "go");
    case "pending":
      return rfps.filter(needsGoNoGoDecision);
    case "in_progress":
      return rfps.filter(isProposalInProgress);
    case "new":
      return rfps.filter(isNewIntake);
    default:
      return rfps;
  }
}

function RfpRowMeta({ rfp }: { rfp: RfpRecord }) {
  const goScore = computeOverallGoScore(
    rfp.fitScore,
    rfp.worthScore,
    rfp.goNoGoAnalysis?.decisionMatrix,
  );
  const scale5 = goScore !== null && goScore <= 5;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
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
      {rfp.pdfUrl ? (
        <a
          href={rfp.pdfUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold text-zo-teal hover:text-zo-orange"
        >
          View PDF →
        </a>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zo-text-muted lg:hidden">
        <span>
          <span className="font-semibold text-foreground">Client:</span>{" "}
          {rfp.client}
        </span>
        <span>
          <span className="font-semibold text-foreground">Due:</span>{" "}
          {formatDate(rfp.dueDate)}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 lg:hidden">
        {rfp.goNoGo === "go" ? <GoSign /> : <GoNoGoBadge recommendation={rfp.goNoGo} />}
        {goScore !== null ? (
          <span className="text-xs font-semibold text-zo-text-secondary">
            Score {scale5 ? `${goScore}/5` : goScore}
          </span>
        ) : null}
      </div>
    </>
  );
}

interface RfpTableProps {
  rfps: RfpRecord[];
  showFilters?: boolean;
}

export function RfpTable({ rfps, showFilters = true }: RfpTableProps) {
  const [activeFilter, setActiveFilter] = useState<FilterTab>("all");

  const filtered = useMemo(
    () => filterRfps(rfps, activeFilter),
    [rfps, activeFilter],
  );

  const tabsWithCounts = filterTabs.map((tab) => ({
    ...tab,
    count: filterRfps(rfps, tab.id).length,
  }));

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-zo-border px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6 sm:py-6 lg:px-8">
        <div>
          <h2 className="font-heading text-xl font-bold text-foreground">
            Active RFPs
          </h2>
          <p className="mt-1 text-sm text-zo-text-muted">
            Fetched via JustWin · {rfps.length} opportunities in pipeline
          </p>
        </div>
      </div>

      {showFilters ? (
        <div className="border-b border-zo-border px-4 py-4 sm:px-6 lg:px-8">
          <OutlineTabs
            tabs={tabsWithCounts}
            activeTab={activeFilter}
            onChange={(id) => setActiveFilter(id as FilterTab)}
          />
        </div>
      ) : null}

      {/* Mobile / tablet cards */}
      <div className="divide-y divide-zo-border lg:hidden">
        {filtered.length === 0 ? (
          <p className="px-4 py-12 text-center text-sm text-zo-text-muted sm:px-6">
            No RFPs match this filter.
          </p>
        ) : (
          filtered.map((rfp) => (
            <motion.article
              key={rfp.id}
              className="group px-4 py-5 sm:px-6"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.32, ease: expoOutEase }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/rfps/${rfp.id}`}
                    className="block font-semibold leading-snug text-foreground transition-colors group-hover:text-zo-orange"
                  >
                    {rfp.title}
                  </Link>
                  <RfpRowMeta rfp={rfp} />
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Link
                    href={`/rfps/${rfp.id}`}
                    className="table-action-btn"
                    aria-label={`Open ${rfp.title}`}
                  >
                    <IconChevron className="h-4 w-4" />
                  </Link>
                  <DeleteRfpButton
                    rfpId={rfp.id}
                    title={rfp.title}
                    variant="table"
                  />
                </div>
              </div>
            </motion.article>
          ))
        )}
      </div>

      {/* Desktop table */}
      <div className="custom-scrollbar hidden overflow-x-auto lg:block">
        <table className="w-full min-w-[880px] text-left">
          <thead>
            <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-[11px] font-bold uppercase tracking-[0.12em] text-zo-text-secondary">
              <th className="px-6 py-4 lg:px-8">RFP</th>
              <th className="px-4 py-4">Client</th>
              <th className="px-4 py-4">Stage</th>
              <th className="px-4 py-4">Due</th>
              <th className="px-4 py-4">Go Score</th>
              <th className="px-4 py-4">Go/No-Go</th>
              <th className="w-24 px-4 py-4 text-right">Actions</th>
            </tr>
          </thead>
          <motion.tbody
            key={activeFilter}
            className="divide-y divide-zo-border"
            initial="hidden"
            animate="visible"
            variants={{
              visible: { transition: { staggerChildren: 0.035 } },
            }}
          >
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
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
                  rfp.goNoGoAnalysis?.decisionMatrix,
                );
                const scale5 = goScore !== null && goScore <= 5;
                return (
                  <motion.tr
                    key={rfp.id}
                    variants={rowVariants}
                    className="group border-b border-zo-border/60 transition-colors duration-200 hover:bg-[var(--zo-hover-bg)]"
                  >
                    <td className="px-6 py-5 lg:px-8">
                      <div className="flex items-start gap-3">
                        {rfp.goNoGo === "go" ? <GoSign className="mt-1 shrink-0" /> : null}
                        <div className="min-w-0">
                          <Link
                            href={`/rfps/${rfp.id}`}
                            className="block max-w-md font-semibold leading-snug text-foreground transition-colors group-hover:text-zo-orange"
                          >
                            {rfp.title}
                          </Link>
                          <RfpRowMeta rfp={rfp} />
                          {rfp.assignedTo ? (
                            <p className="mt-1.5 text-xs text-zo-text-muted">
                              Assigned · {rfp.assignedTo}
                            </p>
                          ) : null}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-5 align-top">
                      <p className="font-medium text-zo-text-secondary">{rfp.client}</p>
                    </td>
                    <td className="px-4 py-5 align-top">
                      <span className="text-sm font-semibold text-foreground">
                        {STAGE_LABELS[rfp.stage]}
                      </span>
                    </td>
                    <td className="px-4 py-5 align-top">
                      <p className="text-sm font-medium">{formatDate(rfp.dueDate)}</p>
                      <p
                        className={`mt-1 text-xs font-semibold ${
                          due.urgent ? "text-zo-danger" : "text-zo-text-muted"
                        }`}
                      >
                        {due.label}
                      </p>
                    </td>
                    <td className="px-4 py-5 align-top">
                      {goScore !== null ? (
                        <span
                          className={`font-heading text-lg font-bold ${
                            scale5
                              ? goScore >= 4
                                ? "text-green-600"
                                : goScore >= 3
                                  ? "text-zo-orange"
                                  : "text-zo-danger"
                              : goScore >= 85
                                ? "text-green-600"
                                : goScore >= 70
                                  ? "text-zo-orange"
                                  : "text-zo-danger"
                          }`}
                        >
                          {scale5 ? `${goScore}/5` : goScore}
                        </span>
                      ) : (
                        <span className="text-zo-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-5 align-top">
                      {rfp.goNoGo === "go" ? (
                        <GoSign />
                      ) : (
                        <GoNoGoBadge recommendation={rfp.goNoGo} />
                      )}
                    </td>
                    <td className="px-4 py-5 align-top">
                      <div className="flex items-center justify-end gap-1 opacity-80 transition-opacity duration-200 group-hover:opacity-100">
                        <Link
                          href={`/rfps/${rfp.id}`}
                          className="table-action-btn"
                          aria-label={`Open ${rfp.title}`}
                        >
                          <IconChevron className="h-4 w-4" />
                        </Link>
                        <DeleteRfpButton
                          rfpId={rfp.id}
                          title={rfp.title}
                          variant="table"
                        />
                      </div>
                    </td>
                  </motion.tr>
                );
              })
            )}
          </motion.tbody>
        </table>
      </div>
    </section>
  );
}
