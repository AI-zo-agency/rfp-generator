"use client";

import { useMemo, useState } from "react";
import type { ComplianceCheckItem, PreSubmitIssue, PreSubmitReview } from "@/types/proposal";
import { buildIssuesMarkdown } from "@/lib/review-markdown";
import { MarkdownReportBody } from "./MarkdownReportBody";

interface ProposalReviewPanelProps {
  review: PreSubmitReview | null;
  rfpClient: string;
  rfpTitle: string;
  isRunning: boolean;
  isAutoFixing?: boolean;
  isFinalizingGaps?: boolean;
  autoFixMode?: "quick" | "ai" | null;
  error: string | null;
  autoFixNotice?: string | null;
  disabled?: boolean;
  onRunReview: () => void;
  onAutoFix?: () => void;
  onFinalizeGaps?: () => void;
  onStopAutoFix?: () => void;
  onJumpToSection?: (sectionId: string) => void;
}

type SeverityFilter = "all" | "critical" | "warning" | "info";

const CATEGORY_LABELS: Record<string, string> = {
  copy_paste: "Wrong client / copy-paste",
  placeholder: "Unfilled placeholders",
  voice: "Voice & tone",
  compliance: "Compliance",
};

const CATEGORY_ORDER = ["copy_paste", "placeholder", "voice", "compliance"];

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

function severityStyles(severity: string): {
  row: string;
  badge: string;
  dot: string;
} {
  if (severity === "critical") {
    return {
      row: "border-red-200/90 bg-red-50/80",
      badge: "bg-red-100 text-red-800",
      dot: "bg-red-500",
    };
  }
  if (severity === "warning") {
    return {
      row: "border-amber-200/90 bg-amber-50/60",
      badge: "bg-amber-100 text-amber-900",
      dot: "bg-amber-500",
    };
  }
  return {
    row: "border-zo-border bg-[#fafbfc]",
    badge: "bg-zo-warm-gray/70 text-zo-text-secondary",
    dot: "bg-zo-text-muted",
  };
}

function ComplianceStatusPill({ status }: { status: ComplianceCheckItem["status"] }) {
  const styles =
    status === "pass"
      ? "bg-emerald-100 text-emerald-800"
      : status === "fail"
        ? "bg-red-100 text-red-800"
        : "bg-amber-100 text-amber-900";
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide ${styles}`}>
      {status}
    </span>
  );
}

export function ProposalReviewPanel({
  review,
  rfpClient,
  rfpTitle,
  isRunning,
  isAutoFixing = false,
  isFinalizingGaps = false,
  autoFixMode = null,
  error,
  autoFixNotice,
  disabled,
  onRunReview,
  onAutoFix,
  onFinalizeGaps,
  onStopAutoFix,
  onJumpToSection,
}: ProposalReviewPanelProps) {
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [expandedCategories, setExpandedCategories] = useState<Set<string> | null>(
    null
  );
  const [showPassingCompliance, setShowPassingCompliance] = useState(false);
  const [showIssuesMarkdown, setShowIssuesMarkdown] = useState(false);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);

  const issuesMarkdown = useMemo(() => {
    if (!review) return "";
    if (review.issuesMarkdown?.trim()) return review.issuesMarkdown;
    return buildIssuesMarkdown(
      rfpClient,
      rfpTitle,
      review.summary,
      review.issues,
      review.complianceChecklist
    );
  }, [review, rfpClient, rfpTitle]);

  const copyIssuesMarkdown = async () => {
    if (!issuesMarkdown) return;
    try {
      await navigator.clipboard.writeText(issuesMarkdown);
      setCopyNotice("Copied issues markdown");
      window.setTimeout(() => setCopyNotice(null), 2200);
    } catch {
      setCopyNotice("Could not copy — select text manually");
    }
  };

  const counts = useMemo(() => {
    if (!review) {
      return { critical: 0, warning: 0, info: 0, complianceFail: 0 };
    }
    return {
      critical: review.issues.filter((i) => i.severity === "critical").length,
      warning: review.issues.filter((i) => i.severity === "warning").length,
      info: review.issues.filter((i) => i.severity === "info").length,
      complianceFail: review.complianceChecklist.filter((c) => c.status === "fail").length,
    };
  }, [review]);

  const filteredIssues = useMemo(() => {
    if (!review) return [];
    if (severityFilter === "all") return review.issues;
    return review.issues.filter((i) => i.severity === severityFilter);
  }, [review, severityFilter]);

  const groupedIssues = useMemo(() => {
    const map = new Map<string, PreSubmitIssue[]>();
    for (const issue of filteredIssues) {
      const key = issue.category || "other";
      const list = map.get(key) ?? [];
      list.push(issue);
      map.set(key, list);
    }
    return [...map.entries()].sort(([a], [b]) => {
      const ai = CATEGORY_ORDER.indexOf(a);
      const bi = CATEGORY_ORDER.indexOf(b);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }, [filteredIssues]);

  const toggleCategory = (category: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  const expandAll = () => {
    setExpandedCategories(new Set(groupedIssues.map(([c]) => c)));
  };

  const collapseAll = () => setExpandedCategories(new Set());

  const isCategoryExpanded = (category: string) =>
    expandedCategories?.has(category) ?? false;

  const complianceVisible = useMemo(() => {
    if (!review) return [];
    if (showPassingCompliance) return review.complianceChecklist;
    return review.complianceChecklist.filter((row) => row.status !== "pass");
  }, [review, showPassingCompliance]);

  const passingComplianceCount = useMemo(() => {
    if (!review) return 0;
    return review.complianceChecklist.filter((row) => row.status === "pass").length;
  }, [review]);

  const canAutoFix =
    Boolean(onAutoFix && review && !review.readyToSubmit && review.issues.length > 0);

  return (
    <div className="proposal-review proposal-review-layout">
      <div className="shrink-0 space-y-3">
        <div className="min-w-0">
          <h2 className="font-heading text-2xl font-bold tracking-tight text-foreground">
            Pre-Submit Review
          </h2>
          <p className="mt-1.5 max-w-xl text-sm text-zo-text-secondary">
            Scan for wrong-client paste, voice issues, and compliance gaps before eVP upload.
          </p>
        </div>

        {isAutoFixing || isFinalizingGaps ? (
          <div className="proposal-review-running flex items-center gap-5 rounded-xl border border-zo-border bg-[#fafbfc] px-5 py-4">
            <span
              className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-zo-orange/30 border-t-zo-orange"
              aria-hidden
            />
            <p className="min-w-0 flex-1 text-sm text-foreground">
              {isFinalizingGaps
                ? "Final editor: Supermemory gap-fill, then MANUAL FILL handoff…"
                : "Fixing only sections with review findings…"}
            </p>
            {onStopAutoFix && isAutoFixing ? (
              <button
                type="button"
                onClick={onStopAutoFix}
                className="zo-btn secondary shrink-0 !border-red-200 !px-5 !py-2.5 !text-red-700 hover:!bg-red-50"
              >
                Stop
              </button>
            ) : null}
          </div>
        ) : (
          <div className="proposal-review-toolbar flex flex-wrap items-center gap-5">
            <button
              type="button"
              onClick={onRunReview}
              disabled={disabled || isRunning}
              className={`zo-btn ${review ? "secondary" : ""}`}
            >
              {isRunning ? "Scanning…" : review ? "Re-run review" : "Run review"}
            </button>
            {canAutoFix ? (
              <button
                type="button"
                onClick={() => onAutoFix!()}
                disabled={disabled || isRunning}
                className="zo-btn"
              >
                Auto-fix issues
              </button>
            ) : null}
            {onFinalizeGaps ? (
              <button
                type="button"
                onClick={() => onFinalizeGaps()}
                disabled={disabled || isRunning}
                className="zo-btn secondary"
                title="Last editor pass: search KB for gap data, then assign Sonja/Ella MANUAL FILL tags for anything KB cannot supply"
              >
                Finalize gaps
              </button>
            ) : null}
          </div>
        )}

        {autoFixNotice && !isAutoFixing && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-relaxed text-emerald-950">
            {autoFixNotice}
          </p>
        )}

        {copyNotice ? (
          <p className="text-xs font-semibold text-zo-orange">{copyNotice}</p>
        ) : null}

        {error && (
          <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-zo-error">
            {error}
          </p>
        )}
      </div>

      {!review && !isRunning && (
        <div className="flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-zo-border bg-[#fafbfc] px-8 py-14 text-center">
          <p className="text-base font-semibold text-foreground">No scan yet</p>
          <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-zo-text-muted">
            Generate your manuscript, then run review to catch wrong-client references before submission.
          </p>
        </div>
      )}

      {review && (
        <div className="proposal-review-split min-h-0 flex-1">
          <aside className="proposal-review-aside custom-scrollbar space-y-3">
            <div
              className={`rounded-2xl border px-4 py-4 ${
                review.readyToSubmit
                  ? "border-emerald-300/80 bg-emerald-50/80"
                  : "border-amber-300/80 bg-amber-50/70"
              }`}
            >
              <p className="text-sm font-bold text-foreground">
                {review.readyToSubmit ? "Ready to submit" : "Issues before upload"}
              </p>
              <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                {review.summary}
              </p>
              <div className="mt-4 flex flex-wrap gap-4 proposal-review-badges">
                <span className="rounded-lg bg-red-100 px-4 py-2 text-xs font-bold text-red-800">
                  {counts.critical} critical
                </span>
                <span className="rounded-lg bg-amber-100 px-4 py-2 text-xs font-bold text-amber-900">
                  {counts.warning} warnings
                </span>
                <span className="rounded-lg bg-zo-warm-gray/70 px-4 py-2 text-xs font-bold text-zo-text-secondary">
                  {review.issues.length} total
                </span>
                {(review.manualFillFlags?.length ?? 0) > 0 && (
                  <span className="rounded-lg bg-violet-100 px-4 py-2 text-xs font-bold text-violet-900">
                    {review.manualFillFlags!.length} manual fill
                  </span>
                )}
              </div>
            </div>

            {review.complianceChecklist.length > 0 && (
              <section className="rounded-2xl border border-zo-border bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-bold uppercase tracking-[0.12em] text-zo-text-muted">
                    Compliance
                  </h3>
                  {passingComplianceCount > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowPassingCompliance((v) => !v)}
                      className="text-xs font-semibold text-zo-orange hover:underline"
                    >
                      {showPassingCompliance ? "Hide" : "Show"} {passingComplianceCount} passing
                    </button>
                  )}
                </div>
                <ul className="mt-3 space-y-2">
                  {complianceVisible.map((row, i) => (
                    <li
                      key={`${row.item}-${i}`}
                      className={`rounded-xl border px-3 py-2.5 ${
                        row.status === "fail"
                          ? "border-red-200 bg-red-50/50"
                          : row.status === "manual"
                            ? "border-amber-200 bg-amber-50/40"
                            : "border-zo-border/60 bg-[#fafbfc]"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium leading-snug text-foreground">
                          {row.item}
                        </p>
                        <ComplianceStatusPill status={row.status} />
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {review.issues.length > 0 && issuesMarkdown ? (
              <section className="rounded-2xl border border-zo-border bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-xs font-bold uppercase tracking-[0.12em] text-zo-text-muted">
                    Issues to fix (markdown)
                  </h3>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void copyIssuesMarkdown()}
                      className="text-xs font-semibold text-zo-orange hover:underline"
                    >
                      Copy
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowIssuesMarkdown((v) => !v)}
                      className="text-xs font-semibold text-zo-text-muted hover:underline"
                    >
                      {showIssuesMarkdown ? "Hide" : "Preview"}
                    </button>
                  </div>
                </div>
                {showIssuesMarkdown ? (
                  <div className="proposal-review-markdown-preview custom-scrollbar mt-3 max-h-64 overflow-y-auto rounded-xl border border-zo-border/70 bg-[#fafbfc] p-3">
                    <MarkdownReportBody body={issuesMarkdown} variant="report" />
                  </div>
                ) : (
                  <p className="mt-2 text-xs leading-relaxed text-zo-text-muted">
                    Generated checklist for auto-fix and handoff. Copy to share with the team or paste into a ticket.
                  </p>
                )}
              </section>
            ) : null}
          </aside>

          {review.issues.length > 0 ? (
            <section className="proposal-review-findings-panel">
              <div className="shrink-0 border-b border-zo-border/60 px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-bold text-foreground">
                    Findings
                    <span className="ml-2 font-normal text-zo-text-muted">
                      ({filteredIssues.length})
                    </span>
                  </h3>
                  <div className="flex items-center gap-2 text-[10px] font-semibold">
                    <button
                      type="button"
                      onClick={expandAll}
                      className="text-zo-orange hover:underline"
                    >
                      Expand all
                    </button>
                    <span className="text-zo-border">·</span>
                    <button
                      type="button"
                      onClick={collapseAll}
                      className="text-zo-text-muted hover:underline"
                    >
                      Collapse
                    </button>
                  </div>
                </div>
                <div className="proposal-review-filters mt-4 flex flex-wrap gap-4">
                  {(
                    [
                      ["all", "All", review.issues.length],
                      ["critical", "Critical", counts.critical],
                      ["warning", "Warnings", counts.warning],
                      ["info", "Info", counts.info],
                    ] as const
                  ).map(([id, label, count]) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setSeverityFilter(id)}
                      className={`proposal-review-filter-btn rounded-lg text-sm font-medium transition-smooth ${
                        severityFilter === id
                          ? "bg-[#ef5018] text-white"
                          : "bg-[#fafbfc] text-zo-text-secondary hover:bg-zo-warm-gray/50"
                      }`}
                    >
                      {label}
                      {count > 0 ? ` (${count})` : ""}
                    </button>
                  ))}
                </div>
              </div>

              <div className="proposal-review-findings-scroll custom-scrollbar px-4 py-3">
                {groupedIssues.length === 0 ? (
                  <p className="py-10 text-center text-sm text-zo-text-muted">
                    No findings at this severity level.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {groupedIssues.map(([category, issues]) => {
                      const expanded = isCategoryExpanded(category);
                      const criticalInGroup = issues.filter(
                        (i) => i.severity === "critical"
                      ).length;
                      return (
                        <div
                          key={category}
                          className="overflow-hidden rounded-xl border border-zo-border/80"
                        >
                          <button
                            type="button"
                            onClick={() => toggleCategory(category)}
                            className="flex w-full items-center justify-between gap-3 bg-[#fafbfc] px-4 py-3 text-left hover:bg-zo-warm-gray/40"
                            aria-expanded={expanded}
                          >
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-foreground">
                                {categoryLabel(category)}
                              </p>
                              <p className="mt-0.5 text-xs text-zo-text-muted">
                                {issues.length} item{issues.length === 1 ? "" : "s"}
                                {criticalInGroup > 0 ? ` · ${criticalInGroup} critical` : ""}
                              </p>
                            </div>
                            <svg
                              className={`h-4 w-4 shrink-0 text-zo-text-muted transition-transform ${
                                expanded ? "rotate-180" : ""
                              }`}
                              fill="none"
                              viewBox="0 0 24 24"
                              stroke="currentColor"
                              strokeWidth={2}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M19.5 8.25l-7.5 7.5-7.5-7.5"
                              />
                            </svg>
                          </button>

                          {expanded && (
                            <ul className="divide-y divide-zo-border/60 border-t border-zo-border/60">
                              {issues.map((issue, i) => {
                                const styles = severityStyles(issue.severity);
                                return (
                                  <li
                                    key={`${issue.message}-${i}`}
                                    className={`px-4 py-3 ${styles.row}`}
                                  >
                                    <div className="flex items-start justify-between gap-3">
                                      <div className="min-w-0 flex-1">
                                        <p className="text-sm leading-snug text-foreground">
                                          {issue.message}
                                        </p>
                                        {issue.sectionTitle ? (
                                          <p className="mt-1 text-xs text-zo-text-secondary">
                                            {issue.sectionTitle}
                                          </p>
                                        ) : null}
                                      </div>
                                      {issue.sectionId && onJumpToSection ? (
                                        <button
                                          type="button"
                                          onClick={() => onJumpToSection(issue.sectionId!)}
                                          className="shrink-0 rounded-lg border border-zo-border bg-white px-3 py-1 text-xs font-semibold text-zo-orange hover:border-zo-orange/40"
                                        >
                                          Open
                                        </button>
                                      ) : null}
                                    </div>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </section>
          ) : (
            <div className="flex items-center justify-center rounded-xl border border-dashed border-zo-border bg-[#fafbfc] p-6 text-xs text-zo-text-muted">
              No findings — check compliance items on the left.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function FeeJustificationBlock({ markdown }: { markdown: string }) {
  if (!markdown.trim()) return null;
  return (
    <div className="rounded-xl border border-zo-border bg-white p-4">
      <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-zo-orange">
        Internal fee justification (not for submission)
      </h3>
      <MarkdownReportBody body={markdown} variant="document" />
    </div>
  );
}
