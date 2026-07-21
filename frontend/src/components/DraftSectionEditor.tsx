"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getTextareaCaretViewportRect, scrollTextareaToRange } from "@/lib/textarea-selection";
import type { FlagHighlightRange } from "@/lib/proposal-manual-flags";
import type { OutlineSection } from "@/types/proposal";
import { MarkdownReportBody, stripEvidenceCitations } from "./MarkdownReportBody";
import type { SectionChatReference } from "./ProposalSectionChatPanel";
import { buildSectionPinReference } from "./ProposalSectionChatPanel";

export interface SectionRevisionRecord {
  before: string;
  after: string;
  summary: string;
  instruction: string;
  updatedAt: number;
}

interface TextSelection {
  text: string;
  start: number;
  end: number;
  top: number;
  left: number;
}

interface DraftSectionEditorProps {
  section: OutlineSection;
  wordCount: number;
  disabled?: boolean;
  chatBusy?: boolean;
  value: string;
  onChange: (content: string) => void;
  onOpenRevisionChat?: (request: SectionChatReference) => void;
  compact?: boolean;
  highlightRange?: FlagHighlightRange | null;
  onUserEditStart?: () => void;
  storedRevision?: SectionRevisionRecord | null;
  revisionDrawerOpen?: boolean;
  onRevisionRecorded?: (revision: SectionRevisionRecord) => void;
  onRevisionDrawerOpenChange?: (open: boolean) => void;
}

function findRangeInContent(content: string, selected: string): { start: number; end: number } | null {
  const trimmed = selected.trim();
  if (trimmed.length < 3) return null;
  const exact = content.indexOf(selected);
  if (exact >= 0) return { start: exact, end: exact + selected.length };
  const loose = content.indexOf(trimmed);
  if (loose >= 0) return { start: loose, end: loose + trimmed.length };
  const collapsed = (s: string) => s.replace(/\s+/g, " ").trim();
  const needle = collapsed(trimmed);
  const hay = collapsed(content);
  const at = hay.indexOf(needle);
  if (at < 0) return null;
  // Approximate back to original offsets via prefix length
  let orig = 0;
  let compact = 0;
  while (orig < content.length && compact < at) {
    if (/\s/.test(content[orig]!)) {
      while (orig < content.length && /\s/.test(content[orig]!)) orig += 1;
      compact += 1;
    } else {
      orig += 1;
      compact += 1;
    }
  }
  const start = orig;
  let end = start;
  let seen = 0;
  while (end < content.length && seen < needle.length) {
    if (/\s/.test(content[end]!)) {
      while (end < content.length && /\s/.test(content[end]!)) end += 1;
      seen += 1;
    } else {
      end += 1;
      seen += 1;
    }
  }
  return end > start ? { start, end } : null;
}

export function DraftSectionEditor({
  section,
  wordCount,
  disabled,
  chatBusy = false,
  value,
  onChange,
  onOpenRevisionChat,
  compact = false,
  highlightRange = null,
  onUserEditStart,
  storedRevision = null,
  revisionDrawerOpen = false,
  onRevisionDrawerOpenChange,
}: DraftSectionEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const selectionRafRef = useRef<number | null>(null);
  const programmaticSelectionRef = useRef(false);
  const appliedHighlightKeyRef = useRef<string | null>(null);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [previewMode, setPreviewMode] = useState(() => Boolean(value));

  const busy = disabled || chatBusy;

  useEffect(() => {
    setSelection(null);
    appliedHighlightKeyRef.current = null;
    setPreviewMode(Boolean(value));
  }, [section.id]);

  useEffect(() => {
    if (!highlightRange || !textareaRef.current || busy) return;
    const ta = textareaRef.current;
    const { start, end } = highlightRange;
    if (start < 0 || end <= start || end > ta.value.length) return;

    const highlightKey = `${section.id}:${start}:${end}:${highlightRange.text}`;
    if (appliedHighlightKeyRef.current === highlightKey) return;
    appliedHighlightKeyRef.current = highlightKey;
    setPreviewMode(false);

    const applyHighlight = () => {
      programmaticSelectionRef.current = true;
      scrollTextareaToRange(ta, start, end);
      window.requestAnimationFrame(() => {
        programmaticSelectionRef.current = false;
      });
    };

    requestAnimationFrame(() => {
      requestAnimationFrame(applyHighlight);
    });
  }, [busy, highlightRange, section.id]);

  const clearSelection = useCallback(() => {
    if (selectionRafRef.current !== null) {
      window.cancelAnimationFrame(selectionRafRef.current);
      selectionRafRef.current = null;
    }
    setSelection(null);
  }, []);

  const captureTextareaSelection = useCallback(() => {
    if (programmaticSelectionRef.current) return;
    if (selectionRafRef.current !== null) {
      window.cancelAnimationFrame(selectionRafRef.current);
    }
    selectionRafRef.current = window.requestAnimationFrame(() => {
      selectionRafRef.current = null;
      const ta = textareaRef.current;
      if (!ta) return;

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      if (start === end) {
        setSelection(null);
        return;
      }

      const text = ta.value.slice(start, end);
      if (text.trim().length < 3) {
        setSelection(null);
        return;
      }

      const coords = getTextareaCaretViewportRect(ta, end);
      setSelection({
        text,
        start,
        end,
        top: coords.top,
        left: coords.left,
      });
    });
  }, []);

  const capturePreviewSelection = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !previewRef.current) {
      return;
    }
    if (!previewRef.current.contains(sel.anchorNode)) return;
    const text = sel.toString();
    if (text.trim().length < 3) {
      setSelection(null);
      return;
    }
    const range = findRangeInContent(value, text);
    if (!range) {
      setSelection(null);
      return;
    }
    try {
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      setSelection({
        text: value.slice(range.start, range.end),
        start: range.start,
        end: range.end,
        top: rect.top,
        left: rect.left + rect.width / 2,
      });
    } catch {
      setSelection(null);
    }
  }, [value]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (target.closest(".proposal-selection-revise-btn")) return;
      if (target === textareaRef.current) return;
      if (previewRef.current?.contains(target)) return;
      clearSelection();
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") clearSelection();
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [clearSelection]);

  useEffect(() => {
    const ta = textareaRef.current;
    const onScroll = () => {
      if (!ta || ta.selectionStart === ta.selectionEnd) return;
      captureTextareaSelection();
    };
    ta?.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      ta?.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [section.id, captureTextareaSelection, previewMode]);

  const openChat = (mode: "selection" | "section") => {
    onUserEditStart?.();
    appliedHighlightKeyRef.current = null;

    if (mode === "selection" && selection) {
      if (!onOpenRevisionChat) return;
      onOpenRevisionChat({
        mode: "selection",
        sectionId: section.id,
        sectionTitle: section.title,
        text: selection.text,
        selection: {
          start: selection.start,
          end: selection.end,
          text: selection.text,
        },
      });
      clearSelection();
      window.getSelection()?.removeAllRanges();
      return;
    }

    // Full-section improve: pin section in assistant (same card as Revise excerpt).
    if (!onOpenRevisionChat) return;
    onOpenRevisionChat(
      buildSectionPinReference(section, value || section.content || "")
    );
  };

  const showRevisePill = Boolean(selection) && !busy;

  return (
    <>
      <div className="proposal-draft-layout">
        <div className={`proposal-draft-main ${compact ? "is-compact" : ""}`}>
          <div className="proposal-draft-toolbar mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-zo-text-muted">
              Draft content
            </span>
            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
              {value ? (
                <button
                  type="button"
                  onClick={() => {
                    if (previewMode) {
                      setPreviewMode(false);
                      window.setTimeout(() => textareaRef.current?.focus(), 50);
                    } else {
                      setPreviewMode(true);
                      clearSelection();
                    }
                  }}
                  className="flex items-center gap-1 rounded-md border border-zo-border bg-zo-surface px-2 py-1 text-[11px] font-semibold text-zo-text-secondary transition-smooth hover:border-zo-orange hover:text-zo-orange"
                >
                  {previewMode ? (
                    <>
                      <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                      Edit
                    </>
                  ) : (
                    <>
                      <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                      Preview
                    </>
                  )}
                </button>
              ) : null}
              {storedRevision && !revisionDrawerOpen ? (
                <button
                  type="button"
                  onClick={() => onRevisionDrawerOpenChange?.(true)}
                  className="proposal-revision-reopen-btn"
                >
                  View what changed
                </button>
              ) : null}
              <button
                type="button"
                disabled={busy || !onOpenRevisionChat}
                onClick={() => openChat("section")}
                className="text-[11px] font-semibold text-zo-orange transition-smooth hover:underline disabled:opacity-50"
              >
                Improve full section
              </button>
              <span className="text-[11px] font-medium text-zo-text-muted">
                {wordCount.toLocaleString()} words
                {section.wordTarget > 0 ? (
                  <span className="text-zo-text-muted/70">
                    {" "}
                    / {section.wordTarget.toLocaleString()} target
                  </span>
                ) : null}
              </span>
            </div>
          </div>

          <div className="proposal-draft-textarea-shell">
            {previewMode && value ? (
              <div
                ref={previewRef}
                className="proposal-draft-preview-pane custom-scrollbar"
                onMouseUp={capturePreviewSelection}
                onKeyUp={capturePreviewSelection}
              >
                <MarkdownReportBody body={stripEvidenceCitations(value)} variant="report" />
              </div>
            ) : (
              <textarea
                ref={textareaRef}
                value={value}
                onChange={(e) => {
                  onUserEditStart?.();
                  appliedHighlightKeyRef.current = null;
                  onChange(e.target.value);
                  setSelection(null);
                }}
                onSelect={captureTextareaSelection}
                onMouseUp={captureTextareaSelection}
                onKeyUp={captureTextareaSelection}
                disabled={busy}
                placeholder="Generate Sections 1–3 or run full proposal to auto-fill, or write manually…"
                className="proposal-draft-textarea zo-input w-full px-3 py-3 text-sm leading-[1.7] text-foreground outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
              />
            )}

            {showRevisePill && selection && typeof document !== "undefined"
              ? createPortal(
                  <button
                    type="button"
                    className="proposal-selection-revise-btn"
                    style={{
                      top: Math.max(12, selection.top - 42),
                      left: selection.left,
                    }}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      openChat("selection");
                    }}
                  >
                    Revise content
                  </button>,
                  document.body
                )
              : null}
          </div>
        </div>
      </div>
    </>
  );
}
