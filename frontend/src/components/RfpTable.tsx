"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "motion/react";
import {
  alignGoNoGoRecommendation,
  computeOverallGoScore,
  daysUntil,
  formatDate,
} from "@/lib/format";
import { expoOutEase } from "@/lib/motion";
import { describeRfpIntake } from "@/lib/rfp-intake";
import {
  getWorkflowStepDisplay,
  isNewIntake,
  isProposalInProgress,
  needsGoNoGoDecision,
} from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";
import { DeleteRfpButton } from "./DeleteRfpButton";
import { GoNoGoBadge } from "./StatusBadge";
import { GoSign } from "./GoSign";
import { OutlineTabs } from "./ui/OutlineTabs";
import { IconTrash } from "./ui/icons";

type FilterTab =
  | "all"
  | "go"
  | "pending"
  | "in_progress"
  | "new"
  | "overdue"
  | "score_gt_3";

const filterTabs: { id: FilterTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "go", label: "Go RFPs" },
  { id: "pending", label: "Needs Decision" },
  { id: "in_progress", label: "Drafting" },
  { id: "new", label: "New Intake" },
  { id: "overdue", label: "Overdue" },
  { id: "score_gt_3", label: "Score > 3" },
];

const rowVariants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.32, ease: expoOutEase },
  },
};

function rfpGoScore(rfp: RfpRecord): number | null {
  return computeOverallGoScore(
    rfp.fitScore,
    rfp.worthScore,
    rfp.goNoGoAnalysis?.decisionMatrix
  );
}

function isOverdueRfp(rfp: RfpRecord): boolean {
  if (!rfp.dueDate) return false;
  return daysUntil(rfp.dueDate).label === "Overdue";
}

function hasScoreGreaterThan3(rfp: RfpRecord): boolean {
  const score = rfpGoScore(rfp);
  return score !== null && score > 3;
}

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
    case "overdue":
      return rfps.filter(isOverdueRfp);
    case "score_gt_3":
      return rfps.filter(hasScoreGreaterThan3);
    default:
      return rfps;
  }
}

function RfpIntakeLine({
  rfp,
  stacked = false,
}: {
  rfp: RfpRecord;
  stacked?: boolean;
}) {
  const intake = describeRfpIntake(rfp);

  if (stacked) {
    return (
      <div
        className="text-[11px] leading-snug text-zo-text-muted"
        title={intake.tooltip}
      >
        <p className="font-semibold text-zo-text-secondary">{intake.method}</p>
        <p className="mt-1">
          {intake.syncedLabel}
          {intake.justwinDateLabel ? (
            <>
              <span className="mx-1.5 text-zo-border">·</span>
              <span className="font-medium text-zo-text-secondary">
                {intake.justwinDateLabel}
              </span>
            </>
          ) : null}
        </p>
      </div>
    );
  }

  return (
    <p
      className="mt-1.5 text-[11px] leading-snug text-zo-text-muted"
      title={intake.tooltip}
    >
      <span className="font-semibold text-zo-text-secondary">{intake.method}</span>
      <span className="mx-1.5 text-zo-border">·</span>
      <span>{intake.syncedLabel}</span>
      {intake.justwinDateLabel ? (
        <>
          <span className="mx-1.5 text-zo-border">·</span>
          <span className="font-medium text-zo-text-secondary">
            {intake.justwinDateLabel}
          </span>
        </>
      ) : null}
    </p>
  );
}

function RfpRowMeta({ rfp }: { rfp: RfpRecord }) {
  const goScore = rfpGoScore(rfp);
  const scale5 = goScore !== null && goScore <= 5;
  const displayGoNoGo = alignGoNoGoRecommendation(rfp.goNoGo, goScore);

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-zo-text-muted">
          {rfp.sector} · {rfp.location}
        </span>
      </div>
      <div className="lg:hidden">
        <RfpIntakeLine rfp={rfp} />
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
        {displayGoNoGo === "go" ? (
          <GoSign />
        ) : (
          <GoNoGoBadge recommendation={displayGoNoGo} />
        )}
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

function matchesSearch(rfp: RfpRecord, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    rfp.title.toLowerCase().includes(q) ||
    rfp.client.toLowerCase().includes(q) ||
    rfp.location.toLowerCase().includes(q) ||
    rfp.sector.toLowerCase().includes(q) ||
    (rfp.description || "").toLowerCase().includes(q)
  );
}

export function RfpTable({ rfps, showFilters = true }: RfpTableProps) {
  const router = useRouter();
  const [activeFilter, setActiveFilter] = useState<FilterTab>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);

  const filtered = useMemo(() => {
    const byTab = filterRfps(rfps, activeFilter);
    return byTab.filter((r) => matchesSearch(r, searchQuery));
  }, [rfps, activeFilter, searchQuery]);

  const tabsWithCounts = filterTabs.map((tab) => ({
    ...tab,
    count: filterRfps(rfps, tab.id).filter((r) =>
      matchesSearch(r, searchQuery)
    ).length,
  }));

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((r) => selectedIds.has(r.id));
  const selectedInView = filtered.filter((r) => selectedIds.has(r.id));

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllFiltered() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const r of filtered) next.delete(r.id);
      } else {
        for (const r of filtered) next.add(r.id);
      }
      return next;
    });
  }

  function changeFilter(id: FilterTab) {
    setActiveFilter(id);
    setSelectedIds(new Set());
    setBulkError(null);
  }

  async function handleBulkDelete() {
    const ids = selectedInView.map((r) => r.id);
    if (ids.length === 0) return;
    setBulkDeleting(true);
    setBulkError(null);
    const failed: string[] = [];
    for (const id of ids) {
      try {
        const response = await fetch(`/api/rfps/${id}`, { method: "DELETE" });
        if (!response.ok) {
          failed.push(id);
        }
      } catch {
        failed.push(id);
      }
    }
    setBulkDeleting(false);
    setBulkConfirmOpen(false);
    if (failed.length > 0) {
      setBulkError(
        `Deleted ${ids.length - failed.length} of ${ids.length}. ${failed.length} failed.`
      );
      setSelectedIds(new Set(failed));
    } else {
      setSelectedIds(new Set());
    }
    router.refresh();
  }

  const bulkModal =
    bulkConfirmOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bulk-delete-rfp-title"
          >
            <button
              type="button"
              className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]"
              aria-label="Close bulk delete confirmation"
              onClick={() => !bulkDeleting && setBulkConfirmOpen(false)}
            />
            <div className="relative z-10 w-full max-w-md rounded-2xl border border-zo-border bg-white p-6 shadow-[0_24px_64px_rgba(15,23,42,0.12)]">
              <h2
                id="bulk-delete-rfp-title"
                className="font-heading text-lg font-bold text-foreground"
              >
                Delete {selectedInView.length} RFP
                {selectedInView.length === 1 ? "" : "s"}?
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                Selected opportunities under the current filter will be removed
                with their proposal drafts and PDFs. This cannot be undone.
              </p>
              <ul className="mt-3 max-h-40 list-disc overflow-y-auto pl-5 text-xs text-zo-text-muted">
                {selectedInView.slice(0, 12).map((r) => (
                  <li key={r.id} className="truncate">
                    {r.title}
                  </li>
                ))}
                {selectedInView.length > 12 ? (
                  <li>…and {selectedInView.length - 12} more</li>
                ) : null}
              </ul>
              <div className="mt-6 flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setBulkConfirmOpen(false)}
                  disabled={bulkDeleting}
                  className="zo-btn secondary !py-2.5 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void handleBulkDelete()}
                  disabled={bulkDeleting}
                  className="zo-btn !border-zo-danger !bg-zo-danger !py-2.5 hover:!bg-red-700 disabled:opacity-50"
                >
                  {bulkDeleting
                    ? "Deleting…"
                    : `Delete ${selectedInView.length}`}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-zo-border px-4 py-5 sm:px-6 sm:py-6 lg:px-8">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-heading text-xl font-bold text-foreground">
              Active RFPs
            </h2>
            <p className="mt-1 text-sm text-zo-text-muted">
              {rfps.length} opportunities in pipeline
              {searchQuery.trim()
                ? ` · ${filtered.length} match${filtered.length === 1 ? "" : "es"}`
                : ""}
            </p>
          </div>
          {selectedInView.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-zo-text-secondary">
                {selectedInView.length} selected
              </span>
              <button
                type="button"
                onClick={() => setBulkConfirmOpen(true)}
                disabled={bulkDeleting}
                className="zo-btn secondary inline-flex items-center gap-2 !border-zo-danger/40 !py-2 !text-xs text-zo-danger hover:!border-zo-danger hover:!bg-zo-danger/8 disabled:opacity-60"
              >
                <IconTrash className="h-3.5 w-3.5" />
                Bulk delete
              </button>
            </div>
          ) : null}
        </div>

        <div className="relative w-full sm:max-w-md">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 z-[1] h-4 w-4 -translate-y-1/2 text-zo-text-muted"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
            />
          </svg>
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setSelectedIds(new Set());
              setBulkError(null);
            }}
            placeholder="Search RFPs by title, client, location…"
            className="zo-input w-full rounded-xl py-2.5 pl-10 pr-3 text-sm outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10 [&::-webkit-search-cancel-button]:hidden"
            aria-label="Search RFPs"
          />
        </div>
      </div>

      {showFilters ? (
        <div className="border-b border-zo-border px-4 py-4 sm:px-6 lg:px-8">
          <OutlineTabs
            tabs={tabsWithCounts}
            activeTab={activeFilter}
            onChange={(id) => changeFilter(id as FilterTab)}
          />
          {(activeFilter === "overdue" || activeFilter === "score_gt_3") &&
          filtered.length > 0 ? (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={toggleAllFiltered}
                className="text-xs font-semibold text-zo-teal hover:text-zo-orange"
              >
                {allFilteredSelected
                  ? "Clear selection"
                  : `Select all ${filtered.length} in this filter`}
              </button>
              <span className="text-[11px] text-zo-text-muted">
                {activeFilter === "overdue"
                  ? "Past due date"
                  : "Overall Go Score greater than 3"}
              </span>
            </div>
          ) : null}
          {bulkError ? (
            <p className="mt-2 text-xs text-zo-danger" role="alert">
              {bulkError}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Mobile / tablet cards */}
      <div className="divide-y divide-zo-border lg:hidden">
        {filtered.length === 0 ? (
          <p className="px-4 py-12 text-center text-sm text-zo-text-muted sm:px-6">
            {searchQuery.trim()
              ? `No RFPs match “${searchQuery.trim()}”.`
              : "No RFPs match this filter."}
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
                <label className="mt-1 shrink-0">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(rfp.id)}
                    onChange={() => toggleOne(rfp.id)}
                    className="h-4 w-4 rounded border-zo-border text-zo-orange focus:ring-zo-orange"
                    aria-label={`Select ${rfp.title}`}
                  />
                </label>
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/rfps/${rfp.id}`}
                    className="block font-semibold leading-snug text-foreground transition-colors group-hover:text-zo-orange"
                  >
                    {rfp.title}
                  </Link>
                  <RfpRowMeta rfp={rfp} />
                </div>
                <div className="flex shrink-0 items-center">
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
        <table className="w-full min-w-[920px] text-left">
          <thead>
            <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-[11px] font-bold uppercase tracking-[0.12em] text-zo-text-secondary">
              <th className="w-10 px-4 py-4 lg:px-6">
                <input
                  type="checkbox"
                  checked={allFilteredSelected}
                  onChange={toggleAllFiltered}
                  disabled={filtered.length === 0}
                  className="h-4 w-4 rounded border-zo-border text-zo-orange focus:ring-zo-orange disabled:opacity-40"
                  aria-label="Select all RFPs in this filter"
                />
              </th>
              <th className="px-4 py-4 lg:px-6">RFP</th>
              <th className="px-4 py-4">Client</th>
              <th className="px-4 py-4">Intake</th>
              <th className="px-4 py-4">Workflow step</th>
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
                  colSpan={9}
                  className="px-8 py-16 text-center text-sm text-zo-text-muted"
                >
                  {searchQuery.trim()
                    ? `No RFPs match “${searchQuery.trim()}”.`
                    : "No RFPs match this filter."}
                </td>
              </tr>
            ) : (
              filtered.map((rfp) => {
                const due = daysUntil(rfp.dueDate);
                const workflow = getWorkflowStepDisplay(rfp);
                const goScore = rfpGoScore(rfp);
                const scale5 = goScore !== null && goScore <= 5;
                const displayGoNoGo = alignGoNoGoRecommendation(
                  rfp.goNoGo,
                  goScore
                );
                return (
                  <motion.tr
                    key={rfp.id}
                    variants={rowVariants}
                    className="group border-b border-zo-border/60 transition-colors duration-200 hover:bg-[var(--zo-hover-bg)]"
                  >
                    <td className="px-4 py-5 align-top lg:px-6">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(rfp.id)}
                        onChange={() => toggleOne(rfp.id)}
                        className="h-4 w-4 rounded border-zo-border text-zo-orange focus:ring-zo-orange"
                        aria-label={`Select ${rfp.title}`}
                      />
                    </td>
                    <td className="px-4 py-5 lg:px-6">
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
                    </td>
                    <td className="px-4 py-5 align-top">
                      <p className="font-medium text-zo-text-secondary">
                        {rfp.client}
                      </p>
                    </td>
                    <td className="px-4 py-5 align-top">
                      <RfpIntakeLine rfp={rfp} stacked />
                    </td>
                    <td className="px-4 py-5 align-top">
                      <p className="text-sm font-semibold text-foreground">
                        {workflow.label}
                      </p>
                      <p
                        className="mt-1 max-w-[14rem] text-xs leading-snug text-zo-text-muted"
                        title={workflow.hint}
                      >
                        {workflow.hint}
                      </p>
                    </td>
                    <td className="px-4 py-5 align-top">
                      <p className="text-sm font-medium">
                        {formatDate(rfp.dueDate)}
                      </p>
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
                      {displayGoNoGo === "go" ? (
                        <GoSign />
                      ) : (
                        <GoNoGoBadge recommendation={displayGoNoGo} />
                      )}
                    </td>
                    <td className="px-4 py-5 align-top">
                      <div className="flex justify-end">
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
      {bulkModal}
    </section>
  );
}
