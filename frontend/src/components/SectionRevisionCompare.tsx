"use client";

import { useEffect, useMemo, useState } from "react";
import { computeTextHunks, countWords, type DiffHunk } from "@/lib/text-diff";
import { MarkdownReportBody } from "./MarkdownReportBody";

export type RevisionCompareTheme = "warm" | "neutral" | "contrast";

interface SectionRevisionCompareProps {
  before: string;
  after: string;
  summary?: string;
  instruction?: string;
  onDismiss: () => void;
}

const THEME_OPTIONS: { id: RevisionCompareTheme; label: string }[] = [
  { id: "warm", label: "Warm" },
  { id: "neutral", label: "Neutral" },
  { id: "contrast", label: "Contrast" },
];

function hunkLabel(type: DiffHunk["type"]): string {
  if (type === "add") return "Added";
  if (type === "remove") return "Removed";
  return "Revised";
}

export function SectionRevisionCompare({
  before,
  after,
  summary,
  instruction,
  onDismiss,
}: SectionRevisionCompareProps) {
  const [theme, setTheme] = useState<RevisionCompareTheme>("warm");
  const [selectedHunk, setSelectedHunk] = useState(0);

  const hunks = useMemo(() => computeTextHunks(before, after), [before, after]);
  const wordsBefore = countWords(before);
  const wordsAfter = countWords(after);
  const wordDelta = wordsAfter - wordsBefore;

  useEffect(() => {
    setSelectedHunk(0);
  }, [before, after]);

  if (hunks.length === 0) return null;

  const active = hunks[selectedHunk] ?? hunks[0];

  return (
    <section
      className={`proposal-revision-drawer-panel proposal-revision-compare--${theme}`}
      aria-label="Section revision summary"
    >
      <header className="proposal-revision-drawer-header">
        <div className="min-w-0 flex-1">
          <p className="proposal-revision-eyebrow">What changed</p>
          <p className="proposal-revision-stats">
            {hunks.length} block{hunks.length === 1 ? "" : "s"} ·{" "}
            {wordDelta >= 0 ? "+" : ""}
            {wordDelta} words ({wordsBefore} → {wordsAfter})
          </p>
          {instruction ? (
            <p className="proposal-revision-request-inline">
              Request: &ldquo;{instruction}&rdquo;
            </p>
          ) : null}
        </div>
        <div className="proposal-revision-drawer-header-actions">
          <div className="proposal-revision-theme-picker">
            {THEME_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                aria-pressed={theme === opt.id}
                onClick={() => setTheme(opt.id)}
                className={theme === opt.id ? "is-active" : ""}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onDismiss}
            className="proposal-revision-dismiss"
            aria-label="Dismiss changes"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </header>

      {summary ? (
        <div className="proposal-revision-meta-row">
          <div className="proposal-revision-summary-md">
            <MarkdownReportBody body={summary} variant="report" />
          </div>
        </div>
      ) : null}

      {hunks.length > 1 ? (
        <div className="proposal-revision-hunk-tabs" role="tablist" aria-label="Changed blocks">
          {hunks.map((hunk, index) => (
            <button
              key={`${hunk.type}-${index}`}
              type="button"
              role="tab"
              aria-selected={selectedHunk === index}
              className={`proposal-revision-hunk-tab proposal-revision-hunk--${hunk.type} ${
                selectedHunk === index ? "is-selected" : ""
              }`}
              onClick={() => setSelectedHunk(index)}
            >
              {hunkLabel(hunk.type)} {index + 1}
            </button>
          ))}
        </div>
      ) : null}

      <div className="proposal-revision-compare-stage">
        {active.before ? (
          <div className="proposal-revision-stage-col proposal-revision-stage-col--before">
            <p className="proposal-revision-stage-label">Before</p>
            <div className="proposal-revision-stage-body">{active.before}</div>
          </div>
        ) : null}
        {active.after ? (
          <div className="proposal-revision-stage-col proposal-revision-stage-col--after">
            <p className="proposal-revision-stage-label">After</p>
            <div className="proposal-revision-stage-body">{active.after}</div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
