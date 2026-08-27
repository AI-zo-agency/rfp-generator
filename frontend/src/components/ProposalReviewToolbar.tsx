"use client";

import { useEffect, useState } from "react";
import {
  selectionHasMarker,
  toggleWrapMarkers,
} from "@/lib/markdown-inline-format";

interface ProposalReviewToolbarProps {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  /** Working markdown for the active section (same string shown in Edit source). */
  content: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  lastSavedAt: number | null;
  onComment: (selectedText: string | null) => void;
  expanded: boolean;
  onToggleExpanded: () => void;
  /** Enter Edit source so a textarea exists for caret/undo sync. */
  ensureEditable?: () => void;
  /** After Bold/Italic/etc., show the formatted view (not raw `**markdown**`). */
  showFormattedView?: () => void;
  /**
   * Selection mapped from the formatted preview (source offsets into `content`).
   * Used when the user highlights in the rendered view, not the textarea.
   */
  previewSelection?: { start: number; end: number } | null;
  onPreviewSelectionConsumed?: () => void;
}

function getLineRange(value: string, caret: number): { start: number; end: number } {
  const start = value.lastIndexOf("\n", caret - 1) + 1;
  const lineEnd = value.indexOf("\n", caret);
  const end = lineEnd === -1 ? value.length : lineEnd;
  return { start, end };
}

/**
 * Uses the deprecated-but-still-supported execCommand for undo/redo so those
 * still hit the browser stack when a textarea is focused.
 */
function formatSavedAgo(savedAtMs: number): string {
  const minutes = Math.floor((Date.now() - savedAtMs) / 60000);
  if (minutes < 1) return "Saved just now";
  if (minutes < 60) return `Saved ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `Saved ${hours}h ago`;
}

function ToolbarButton({
  label,
  hint,
  onClick,
  disabled,
  pressed,
  children,
}: {
  label: string;
  /** Longer hover explanation. */
  hint: string;
  onClick: () => void;
  disabled?: boolean;
  /** Shown when the current selection already has this format (toggle off). */
  pressed?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={hint}
      aria-label={label}
      aria-pressed={pressed ? true : undefined}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`proposal-workflow-toolbar-btn${
        pressed
          ? " border-[rgba(17,24,39,0.18)] bg-[rgba(17,24,39,0.08)] text-foreground"
          : ""
      }`}
    >
      {children}
    </button>
  );
}

export function ProposalReviewToolbar({
  textareaRef,
  content,
  onChange,
  disabled = false,
  lastSavedAt,
  onComment,
  expanded,
  onToggleExpanded,
  ensureEditable,
  showFormattedView,
  previewSelection = null,
  onPreviewSelectionConsumed,
}: ProposalReviewToolbarProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const resolveRange = (): { start: number; end: number; value: string } | null => {
    const ta = textareaRef.current;
    // Only trust a live Edit-source textarea — a detached node keeps a stale
    // selection and blocks toggle after we leave formatted view.
    if (
      ta?.isConnected &&
      ta.selectionStart !== ta.selectionEnd
    ) {
      return {
        start: ta.selectionStart,
        end: ta.selectionEnd,
        value: ta.value,
      };
    }
    const value = content;
    if (
      previewSelection &&
      previewSelection.start >= 0 &&
      previewSelection.end > previewSelection.start &&
      previewSelection.end <= value.length
    ) {
      return {
        start: previewSelection.start,
        end: previewSelection.end,
        value,
      };
    }
    return null;
  };

  /** One write to markdown, then show the formatted view (bold looks bold). */
  const commitValue = (next: string) => {
    onChange(next);
    onPreviewSelectionConsumed?.();
    // Stay out of raw Edit source — asterisks are storage, not what the user wants to see.
    showFormattedView?.();
  };

  const commitEdit = (
    value: string,
    start: number,
    end: number,
    replacement: string
  ) => {
    commitValue(value.slice(0, start) + replacement + value.slice(end));
  };

  const wrapSelection = (marker: string) => {
    const range = resolveRange();
    const value = range?.value ?? content;
    let start = range?.start ?? value.length;
    let end = range?.end ?? value.length;
    if (start > end) [start, end] = [end, start];
    const { next } = toggleWrapMarkers(value, start, end, marker);
    commitValue(next);
  };

  const rangeNow = resolveRange();
  const boldPressed = Boolean(
    rangeNow &&
      selectionHasMarker(rangeNow.value, rangeNow.start, rangeNow.end, "**")
  );
  const italicPressed = Boolean(
    rangeNow &&
      selectionHasMarker(rangeNow.value, rangeNow.start, rangeNow.end, "_")
  );

  const applyLinePrefix = (computePrefix: (lineIndex: number) => string) => {
    const range = resolveRange();
    const value = range?.value ?? content;
    const caretStart = range?.start ?? 0;
    const caretEnd = range?.end ?? caretStart;
    const blockStart = getLineRange(value, caretStart).start;
    const blockEnd = getLineRange(value, Math.max(caretEnd - 1, caretStart)).end;
    const lines = value.slice(blockStart, blockEnd).split("\n");
    const nextBlock = lines
      .map((line, i) => {
        const stripped = line.replace(/^(\s*)(?:[-*+]\s+|\d+\.\s+)/, "$1");
        return `${computePrefix(i)}${stripped}`;
      })
      .join("\n");
    commitEdit(value, blockStart, blockEnd, nextBlock);
  };

  const setHeading = (marker: string) => {
    const range = resolveRange();
    const value = range?.value ?? content;
    const caret = range?.start ?? 0;
    const { start, end } = getLineRange(value, caret);
    const stripped = value.slice(start, end).replace(/^#{1,6}\s*/, "");
    commitEdit(value, start, end, marker ? `${marker} ${stripped}` : stripped);
  };

  const insertLink = () => {
    const range = resolveRange();
    const value = range?.value ?? content;
    const start = range?.start ?? value.length;
    const end = range?.end ?? start;
    const label = value.slice(start, end) || "link text";
    const replacement = `[${label}](url)`;
    commitEdit(value, start, end, replacement);
  };

  const undo = () => {
    ensureEditable?.();
    textareaRef.current?.focus();
    document.execCommand("undo");
  };

  const redo = () => {
    ensureEditable?.();
    textareaRef.current?.focus();
    document.execCommand("redo");
  };

  const handleComment = () => {
    const range = resolveRange();
    const ta = textareaRef.current;
    if (range) {
      onComment(range.value.slice(range.start, range.end));
      return;
    }
    const hasSelection = ta && ta.selectionStart !== ta.selectionEnd;
    onComment(hasSelection ? ta.value.slice(ta.selectionStart, ta.selectionEnd) : null);
  };

  return (
    <div className="proposal-workflow-toolbar" role="toolbar" aria-label="Formatting">
      <select
        className="proposal-workflow-toolbar-select"
        disabled={disabled}
        defaultValue=""
        title="Change this line to a heading or paragraph"
        aria-label="Block style"
        onChange={(e) => {
          setHeading(e.target.value);
          e.target.value = "";
        }}
      >
        <option value="">Paragraph</option>
        <option value="##">Heading 2</option>
        <option value="###">Heading 3</option>
      </select>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton
        label="Bold"
        hint={
          boldPressed
            ? "Bold — click again to remove bold"
            : "Bold — make the highlighted text look bold"
        }
        disabled={disabled}
        pressed={boldPressed}
        onClick={() => wrapSelection("**")}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}>
          <path d="M6 4h7a3.5 3.5 0 010 7H6zM6 11h8a3.5 3.5 0 010 7H6z" strokeLinejoin="round" />
        </svg>
      </ToolbarButton>
      <ToolbarButton
        label="Italic"
        hint={
          italicPressed
            ? "Italic — click again to remove italic"
            : "Italic — make the highlighted text italic"
        }
        disabled={disabled}
        pressed={italicPressed}
        onClick={() => wrapSelection("_")}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
          <path d="M11 4h6M7 20h6M14 4L10 20" strokeLinecap="round" />
        </svg>
      </ToolbarButton>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton
        label="Bullet list"
        hint="Bullet list — turn the selected lines into bullets"
        disabled={disabled}
        onClick={() => applyLinePrefix(() => "- ")}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <circle cx="4.5" cy="6" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="4.5" cy="18" r="1.2" fill="currentColor" stroke="none" />
          <path d="M9 6h11M9 12h11M9 18h11" strokeLinecap="round" />
        </svg>
      </ToolbarButton>
      <ToolbarButton
        label="Numbered list"
        hint="Numbered list — turn the selected lines into 1, 2, 3…"
        disabled={disabled}
        onClick={() => applyLinePrefix((i) => `${i + 1}. `)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M9 6h11M9 12h11M9 18h11" strokeLinecap="round" />
          <text x="1" y="8" fontSize="7" fill="currentColor" stroke="none">1</text>
          <text x="1" y="14" fontSize="7" fill="currentColor" stroke="none">2</text>
          <text x="1" y="20" fontSize="7" fill="currentColor" stroke="none">3</text>
        </svg>
      </ToolbarButton>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton
        label="Link"
        hint="Link — turn the highlighted text into a web link"
        disabled={disabled}
        onClick={insertLink}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path
            d="M9.5 14.5l5-5M8 17l-2 2a3.5 3.5 0 01-5-5l4-4a3.5 3.5 0 015 0M16 7l2-2a3.5 3.5 0 015 5l-4 4a3.5 3.5 0 01-5 0"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </ToolbarButton>
      <ToolbarButton
        label="Discuss with AI"
        hint="Discuss with AI — open chat about the highlighted text"
        disabled={disabled}
        onClick={handleComment}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path
            d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </ToolbarButton>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton
        label="Undo"
        hint="Undo — reverse the last edit"
        disabled={disabled}
        onClick={undo}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M9 7L4 12l5 5M4 12h11a5 5 0 010 10h-1" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </ToolbarButton>
      <ToolbarButton
        label="Redo"
        hint="Redo — bring back the last undone edit"
        disabled={disabled}
        onClick={redo}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M15 7l5 5-5 5M20 12H9A5 5 0 009 22h1" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </ToolbarButton>

      <div className="proposal-workflow-toolbar-spacer" />

      <span className="proposal-workflow-toolbar-saved">
        {lastSavedAt ? formatSavedAgo(lastSavedAt) : "Not saved yet"}
      </span>

      <ToolbarButton
        label={expanded ? "Collapse" : "Expand"}
        hint={
          expanded
            ? "Collapse — show the side panels again"
            : "Expand — widen the writing area"
        }
        onClick={onToggleExpanded}
      >
        {expanded ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M3 9V3h6M21 9V3h-6M3 15v6h6M21 15v6h-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </ToolbarButton>
    </div>
  );
}
