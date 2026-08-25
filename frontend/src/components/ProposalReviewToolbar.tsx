"use client";

import { useEffect, useState } from "react";

interface ProposalReviewToolbarProps {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  disabled?: boolean;
  lastSavedAt: number | null;
  onComment: (selectedText: string | null) => void;
  expanded: boolean;
  onToggleExpanded: () => void;
}

function getLineRange(value: string, caret: number): { start: number; end: number } {
  const start = value.lastIndexOf("\n", caret - 1) + 1;
  const lineEnd = value.indexOf("\n", caret);
  const end = lineEnd === -1 ? value.length : lineEnd;
  return { start, end };
}

/**
 * Uses the deprecated-but-still-supported execCommand("insertText", ...) so edits
 * land on the browser's native undo/redo stack — a plain value/onChange replace
 * would otherwise silently break Ctrl+Z for toolbar-driven edits.
 */
function insertText(textarea: HTMLTextAreaElement, text: string): boolean {
  textarea.focus();
  try {
    return document.execCommand("insertText", false, text);
  } catch {
    return false;
  }
}

function formatSavedAgo(savedAtMs: number): string {
  const minutes = Math.floor((Date.now() - savedAtMs) / 60000);
  if (minutes < 1) return "Saved just now";
  if (minutes < 60) return `Saved ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `Saved ${hours}h ago`;
}

function ToolbarButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className="proposal-workflow-toolbar-btn"
    >
      {children}
    </button>
  );
}

export function ProposalReviewToolbar({
  textareaRef,
  onChange,
  disabled = false,
  lastSavedAt,
  onComment,
  expanded,
  onToggleExpanded,
}: ProposalReviewToolbarProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const replaceRange = (start: number, end: number, text: string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.setSelectionRange(start, end);
    if (!insertText(ta, text)) {
      const next = ta.value.slice(0, start) + text + ta.value.slice(end);
      onChange(next);
      window.requestAnimationFrame(() => {
        ta.setSelectionRange(start, start + text.length);
        ta.focus();
      });
    }
  };

  const wrapSelection = (marker: string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const { selectionStart, selectionEnd, value } = ta;
    const selected = value.slice(selectionStart, selectionEnd);
    const placeholder = selected || "text";
    replaceRange(selectionStart, selectionEnd, `${marker}${placeholder}${marker}`);
    if (!selected) {
      window.requestAnimationFrame(() => {
        ta.setSelectionRange(
          selectionStart + marker.length,
          selectionStart + marker.length + placeholder.length
        );
        ta.focus();
      });
    }
  };

  const applyLinePrefix = (computePrefix: (lineIndex: number) => string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const { selectionStart, selectionEnd, value } = ta;
    const blockStart = getLineRange(value, selectionStart).start;
    const blockEnd = getLineRange(value, Math.max(selectionEnd - 1, selectionStart)).end;
    const lines = value.slice(blockStart, blockEnd).split("\n");
    const nextBlock = lines.map((line, i) => `${computePrefix(i)}${line}`).join("\n");
    replaceRange(blockStart, blockEnd, nextBlock);
  };

  const setHeading = (marker: string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const { start, end } = getLineRange(ta.value, ta.selectionStart);
    const stripped = ta.value.slice(start, end).replace(/^#{1,6}\s*/, "");
    replaceRange(start, end, marker ? `${marker} ${stripped}` : stripped);
  };

  const insertLink = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    const { selectionStart, selectionEnd, value } = ta;
    const label = value.slice(selectionStart, selectionEnd) || "link text";
    replaceRange(selectionStart, selectionEnd, `[${label}](url)`);
    const urlStart = selectionStart + label.length + 3;
    window.requestAnimationFrame(() => {
      ta.setSelectionRange(urlStart, urlStart + 3);
      ta.focus();
    });
  };

  const undo = () => {
    textareaRef.current?.focus();
    document.execCommand("undo");
  };

  const redo = () => {
    textareaRef.current?.focus();
    document.execCommand("redo");
  };

  const handleComment = () => {
    const ta = textareaRef.current;
    const hasSelection = ta && ta.selectionStart !== ta.selectionEnd;
    onComment(hasSelection ? ta.value.slice(ta.selectionStart, ta.selectionEnd) : null);
  };

  return (
    <div className="proposal-workflow-toolbar" role="toolbar" aria-label="Formatting">
      <select
        className="proposal-workflow-toolbar-select"
        disabled={disabled}
        defaultValue=""
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

      <ToolbarButton label="Bold" disabled={disabled} onClick={() => wrapSelection("**")}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}>
          <path d="M6 4h7a3.5 3.5 0 010 7H6zM6 11h8a3.5 3.5 0 010 7H6z" strokeLinejoin="round" />
        </svg>
      </ToolbarButton>
      <ToolbarButton label="Italic" disabled={disabled} onClick={() => wrapSelection("_")}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
          <path d="M11 4h6M7 20h6M14 4L10 20" strokeLinecap="round" />
        </svg>
      </ToolbarButton>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton
        label="Bullet list"
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

      <ToolbarButton label="Link" disabled={disabled} onClick={insertLink}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path
            d="M9.5 14.5l5-5M8 17l-2 2a3.5 3.5 0 01-5-5l4-4a3.5 3.5 0 015 0M16 7l2-2a3.5 3.5 0 015 5l-4 4a3.5 3.5 0 01-5 0"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </ToolbarButton>
      <ToolbarButton label="Discuss with AI" disabled={disabled} onClick={handleComment}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path
            d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </ToolbarButton>

      <span className="proposal-workflow-toolbar-sep" aria-hidden />

      <ToolbarButton label="Undo" disabled={disabled} onClick={undo}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M9 7L4 12l5 5M4 12h11a5 5 0 010 10h-1" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </ToolbarButton>
      <ToolbarButton label="Redo" disabled={disabled} onClick={redo}>
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
