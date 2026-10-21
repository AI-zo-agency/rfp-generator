"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { createMarkdownSourceMap } from "@/lib/markdown-source-map";
import { getTextareaCaretViewportRect } from "@/lib/textarea-selection";
import type { FlagHighlightRange } from "@/lib/proposal-manual-flags";
import type {
  EvidenceItem,
  OutlineSection,
  ProposalOutline,
  ProposalResearch,
} from "@/types/proposal";
import { repairLeakedHtmlInMarkdown } from "@/lib/markdown-rich-text";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { ProposalSectionRichEditor } from "./ProposalSectionRichEditor";
import type { SectionChatReference } from "./ProposalSectionChatPanel";
import {
  buildSectionPinReference,
  buildSelectionPinReference,
} from "./ProposalSectionChatPanel";
import { CapabilityHoverTip } from "./CapabilityHoverTip";
import { capabilityById } from "@/lib/proposal-tool-guide";

/** preview = formatted read-only; rich = TipTap WYSIWYG; source = raw markdown. */
export type SectionBodyMode = "preview" | "rich" | "source";

export interface SectionRevisionRecord {
  before: string;
  after: string;
  summary: string;
  instruction: string;
  updatedAt: number;
  /** True when AI proposed a change but the live draft is still Before. */
  awaitingConfirm?: boolean;
  /** Full preview draft to persist on Apply (includes multi-section edits). */
  pendingDraft?: ProposalOutline;
  pendingResearch?: ProposalResearch | null;
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
  /** Parent chrome owns Edit / Improve; keep the manuscript itself here. */
  hideToolbar?: boolean;
  bodyMode?: SectionBodyMode;
  onBodyModeChange?: (mode: SectionBodyMode) => void;
  /** @deprecated Prefer bodyMode — true maps to preview, false to source. */
  previewMode?: boolean;
  onPreviewModeChange?: (preview: boolean) => void;
  evidenceCorpus?: EvidenceItem[];
  citationMap?: OutlineSection["citationMap"];
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
  hideToolbar = false,
  bodyMode: bodyModeProp,
  onBodyModeChange,
  previewMode: previewModeProp,
  onPreviewModeChange,
  evidenceCorpus,
  citationMap,
}: DraftSectionEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const selectionRafRef = useRef<number | null>(null);
  const programmaticSelectionRef = useRef(false);
  const appliedHighlightKeyRef = useRef<string | null>(null);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [uncontrolledMode, setUncontrolledMode] = useState<SectionBodyMode>(() =>
    value ? "preview" : "rich",
  );
  const modeControlled =
    bodyModeProp !== undefined && typeof onBodyModeChange === "function";
  const legacyControlled =
    !modeControlled &&
    previewModeProp !== undefined &&
    typeof onPreviewModeChange === "function";
  const bodyMode: SectionBodyMode = modeControlled
    ? (bodyModeProp as SectionBodyMode)
    : legacyControlled
      ? previewModeProp
        ? "preview"
        : "source"
      : uncontrolledMode;
  const setBodyMode = (next: SectionBodyMode) => {
    if (modeControlled) onBodyModeChange?.(next);
    else if (legacyControlled) onPreviewModeChange?.(next === "preview");
    else setUncontrolledMode(next);
  };

  const busy = disabled || chatBusy;

  const displayValue = useMemo(
    () => repairLeakedHtmlInMarkdown(value),
    [value],
  );

  // Persist TipTap HTML / leftover td-tr fragments so Preview stays clean.
  useEffect(() => {
    if (displayValue === value) return;
    onUserEditStart?.();
    onChange(displayValue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section.id, value, displayValue]);

  // Preview selections arrive as rendered text; this maps them back onto raw
  // markdown offsets. Projected once per section body, not once per mouseup.
  const sourceMap = useMemo(
    () => createMarkdownSourceMap(displayValue),
    [displayValue],
  );

  useEffect(() => {
    setSelection(null);
    appliedHighlightKeyRef.current = null;
    if (!modeControlled && !legacyControlled) {
      setUncontrolledMode(value ? "preview" : "rich");
    }
    // Only when the open section changes — not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section.id, modeControlled, legacyControlled]);

  useEffect(() => {
    if (!highlightRange || busy) return;

    const highlightKey = `${section.id}:${highlightRange.start}:${highlightRange.end}:${highlightRange.text}`;
    if (appliedHighlightKeyRef.current === highlightKey) return;
    appliedHighlightKeyRef.current = highlightKey;
    setBodyMode("preview");
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
    if (!sel || sel.isCollapsed || sel.rangeCount === 0 || !previewRef.current) {
      return;
    }
    if (!previewRef.current.contains(sel.anchorNode)) return;
    const text = sel.toString().replace(/\u00a0/g, " ");
    const trimmed = text.trim();
    // Table cells / short labels still deserve Ask to change.
    if (trimmed.length < 1) {
      setSelection(null);
      return;
    }

    let rect: DOMRect | null = null;
    try {
      const range0 = sel.getRangeAt(0);
      rect = range0.getBoundingClientRect();
      // HTML table / multi-node selections often report a 0×0 bounding box.
      if (rect.width < 1 && rect.height < 1) {
        const hit = Array.from(range0.getClientRects()).find(
          (r) => r.width >= 1 || r.height >= 1
        );
        if (hit) rect = hit;
      }
      if (rect.width < 1 && rect.height < 1) {
        const el =
          sel.anchorNode instanceof Element
            ? sel.anchorNode
            : sel.anchorNode?.parentElement ?? null;
        const er = el?.getBoundingClientRect();
        if (er && (er.width >= 1 || er.height >= 1)) rect = er;
      }
    } catch {
      rect = null;
    }
    if (!rect || (rect.width < 1 && rect.height < 1)) {
      setSelection(null);
      return;
    }

    // Table cells render humanized MANUAL FILL chrome ("TBD — …") that will
    // not map 1:1 onto markdown — still show the tip with the visible excerpt.
    const range = sourceMap.find(text);
    setSelection({
      text: range ? displayValue.slice(range.start, range.end) : trimmed,
      start: range?.start ?? 0,
      end: range?.end ?? 0,
      top: rect.top,
      left: rect.left + Math.max(rect.width, 8) / 2,
    });
  }, [sourceMap, displayValue]);
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
  }, [section.id, captureTextareaSelection, bodyMode]);

  const openChat = (mode: "selection" | "section") => {
    onUserEditStart?.();
    appliedHighlightKeyRef.current = null;

    if (mode === "selection" && selection) {
      if (!onOpenRevisionChat) return;
      const mapped = selection.end > selection.start;
      onOpenRevisionChat(
        buildSelectionPinReference(
          section,
          selection.text,
          mapped
            ? { start: selection.start, end: selection.end }
            : undefined,
        ),
      );
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
          {!hideToolbar ? (
          <div className="proposal-draft-toolbar mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-zo-text-muted">
              Draft content
            </span>
            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
              {value ? (
                <CapabilityHoverTip id="editPreview" side="bottom">
                  <button
                    type="button"
                    onClick={() => {
                      if (bodyMode === "preview") {
                        setBodyMode("rich");
                      } else {
                        setBodyMode("preview");
                        clearSelection();
                      }
                    }}
                    className="flex items-center gap-1 rounded-md border border-zo-border bg-zo-surface px-2 py-1 text-[11px] font-semibold text-zo-text-secondary transition-smooth hover:border-zo-orange hover:text-zo-orange"
                  >
                    {bodyMode === "preview" ? (
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
                </CapabilityHoverTip>
              ) : null}
              {bodyMode === "rich" || bodyMode === "preview" ? (
                <button
                  type="button"
                  onClick={() => {
                    setBodyMode("source");
                    window.setTimeout(() => textareaRef.current?.focus(), 50);
                  }}
                  className="rounded-md border border-zo-border bg-zo-surface px-2 py-1 text-[11px] font-semibold text-zo-text-muted transition-smooth hover:border-zo-orange hover:text-zo-orange"
                >
                  Edit source
                </button>
              ) : null}
              {storedRevision && !revisionDrawerOpen ? (
                <button
                  type="button"
                  onClick={() => onRevisionDrawerOpenChange?.(true)}
                  className="proposal-revision-reopen-btn"
                >
                  {storedRevision.awaitingConfirm
                    ? "View changes · Apply pending"
                    : "View what changed"}
                </button>
              ) : null}
              <CapabilityHoverTip id="improveSection" side="bottom">
                <button
                  type="button"
                  disabled={busy || !onOpenRevisionChat}
                  onClick={() => openChat("section")}
                  className="flex items-center gap-1 rounded-md border border-zo-orange/35 bg-zo-orange/10 px-2 py-1 text-[11px] font-semibold text-zo-orange transition-smooth hover:border-zo-orange hover:bg-zo-orange/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/></svg>
                  Improve full section
                </button>
              </CapabilityHoverTip>
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
          ) : null}

          <div className="proposal-draft-textarea-shell">
            {bodyMode === "preview" && value ? (
              <div
                ref={previewRef}
                className="proposal-draft-preview-pane custom-scrollbar"
                onMouseUp={capturePreviewSelection}
                onKeyUp={capturePreviewSelection}
              >
                <MarkdownReportBody
                  body={displayValue}
                  variant="report"
                  evidenceCorpus={evidenceCorpus}
                  citationMap={citationMap}
                  highlightTexts={highlightRange ? [highlightRange.text] : []}
                />
              </div>
            ) : bodyMode === "rich" ? (
              <ProposalSectionRichEditor
                value={value}
                disabled={busy}
                onUserEditStart={onUserEditStart}
                onChange={(content) => {
                  appliedHighlightKeyRef.current = null;
                  onChange(content);
                  setSelection(null);
                }}
                placeholder="Run Build my proposal to auto-fill, or write manually…"
              />
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
                placeholder="Run Build my proposal to auto-fill, or write manually…"
                className="proposal-draft-textarea zo-input w-full px-3 py-3 text-sm leading-[1.7] text-foreground outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
              />
            )}

            {showRevisePill && selection && typeof document !== "undefined"
              ? createPortal(
                  <div
                    role="toolbar"
                    aria-label="Selection actions"
                    className="proposal-selection-revise-btn"
                    style={{
                      top: Math.max(12, selection.top - 42),
                      left: selection.left,
                      zIndex: 400,
                      display: "flex",
                      alignItems: "center",
                      gap: "0.25rem",
                      padding: "0.2rem 0.35rem",
                    }}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                  >
                    <button
                      type="button"
                      className="flex items-center gap-1 rounded-md px-1 py-1 text-[11px] font-semibold text-[var(--zo-orange)]"
                      title={`${capabilityById("reviseSelection").does} ${capabilityById("reviseSelection").doesnt}`}
                      onClick={() => openChat("selection")}
                    >
                      <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
                      Ask to change this
                    </button>
                  </div>,
                  document.body
                )
              : null}
          </div>
        </div>
      </div>
    </>
  );
}
