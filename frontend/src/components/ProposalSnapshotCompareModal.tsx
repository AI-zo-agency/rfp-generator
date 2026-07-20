"use client";

import { useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  computeTextHunks,
  countWords,
  inlineDiffSegments,
  type DiffHunk,
} from "@/lib/text-diff";

export interface ProposalSnapshotCompareModalProps {
  open: boolean;
  onClose: () => void;
  sectionTitle: string;
  snapshotLabel: string;
  before: string;
  after: string;
}

function hunkLabel(type: DiffHunk["type"]): string {
  if (type === "add") return "Added";
  if (type === "remove") return "Removed";
  return "Changed";
}

function HighlightedSide({
  text,
  other,
  side,
}: {
  text: string | undefined;
  other: string | undefined;
  side: "before" | "after";
}) {
  if (!text?.trim()) {
    return <span className="text-zo-text-muted italic">—</span>;
  }
  const otherText = other ?? "";
  const segments = inlineDiffSegments(text, otherText, side);
  return (
    <>
      {segments.map((seg, i) =>
        seg.highlight ? (
          <mark
            key={i}
            className={
              seg.highlight === "remove"
                ? "proposal-diff-mark proposal-diff-mark--remove"
                : "proposal-diff-mark proposal-diff-mark--add"
            }
          >
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </>
  );
}

function SideBySideBlock({
  hunk,
  index,
  snapshotLabel,
}: {
  hunk: DiffHunk;
  index: number;
  snapshotLabel: string;
}) {
  return (
    <div className="proposal-snapshot-compare-block">
      {hunk.type !== "equal" ? (
        <p className="proposal-snapshot-compare-block-tag">
          {hunkLabel(hunk.type)} · block {index + 1}
        </p>
      ) : null}
      <div className="proposal-revision-compare-stage proposal-snapshot-compare-stage">
        <div className="proposal-revision-stage-col proposal-revision-stage-col--before">
          <p className="proposal-revision-stage-label">{snapshotLabel}</p>
          <div className="proposal-revision-stage-body">
            <HighlightedSide text={hunk.before} other={hunk.after} side="before" />
          </div>
        </div>
        <div className="proposal-revision-stage-col proposal-revision-stage-col--after">
          <p className="proposal-revision-stage-label">Current</p>
          <div className="proposal-revision-stage-body">
            <HighlightedSide text={hunk.after} other={hunk.before} side="after" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function ProposalSnapshotCompareModal({
  open,
  onClose,
  sectionTitle,
  snapshotLabel,
  before,
  after,
}: ProposalSnapshotCompareModalProps) {
  const hunks = useMemo(() => {
    const h = computeTextHunks(before, after);
    if (h.length > 0) return h;
    if (before.trim() || after.trim()) {
      return [
        {
          type: (before.trim() && after.trim()
            ? "change"
            : before.trim()
              ? "remove"
              : "add") as DiffHunk["type"],
          before: before.trim() || undefined,
          after: after.trim() || undefined,
        },
      ];
    }
    return [];
  }, [before, after]);

  const wordsBefore = countWords(before);
  const wordsAfter = countWords(after);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <>
      <button
        type="button"
        className="proposal-snapshot-compare-backdrop"
        aria-label="Close comparison"
        onClick={onClose}
      />
      <div
        className="proposal-snapshot-compare-modal proposal-revision-compare--warm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="snapshot-compare-title"
      >
        <header className="proposal-snapshot-compare-header">
          <div className="min-w-0 flex-1">
            <p id="snapshot-compare-title" className="text-base font-bold text-foreground">
              {sectionTitle}
            </p>
            <p className="mt-1 text-xs text-zo-text-muted">
              {snapshotLabel} → current · {wordsBefore.toLocaleString()} →{" "}
              {wordsAfter.toLocaleString()} words
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="proposal-manual-flags-drawer-close shrink-0"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="proposal-snapshot-compare-body custom-scrollbar">
          {hunks.length === 0 ? (
            <p className="p-6 text-sm text-zo-text-muted">No text differences in this section.</p>
          ) : (
            hunks.map((hunk, index) => (
              <SideBySideBlock
                key={`${hunk.type}-${index}`}
                hunk={hunk}
                index={index}
                snapshotLabel={snapshotLabel}
              />
            ))
          )}
        </div>
      </div>
    </>,
    document.body
  );
}
