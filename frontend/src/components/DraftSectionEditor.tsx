"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { improveProposalSection } from "@/lib/proposal-api";
import { getTextareaCaretViewportRect, scrollTextareaToRange } from "@/lib/textarea-selection";
import type { FlagHighlightRange } from "@/lib/proposal-manual-flags";
import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import { SectionRevisionCompare } from "./SectionRevisionCompare";

interface TextSelection {
  text: string;
  start: number;
  end: number;
  top: number;
  left: number;
}

interface DraftSectionEditorProps {
  rfpId: string;
  section: OutlineSection;
  wordCount: number;
  disabled?: boolean;
  value: string;
  onChange: (content: string) => void;
  onSectionUpdated: (draft: ProposalOutline, research: ProposalResearch | null) => void;
  compact?: boolean;
  highlightRange?: FlagHighlightRange | null;
  onUserEditStart?: () => void;
}

const SECTION_PROMPTS = [
  "Re-search with more detailed queries and strengthen this section.",
  "Add verified case studies and outcomes from the knowledge base.",
  "Make this more specific to the client — less generic.",
];

export function DraftSectionEditor({
  rfpId,
  section,
  wordCount,
  disabled,
  value,
  onChange,
  onSectionUpdated,
  compact = false,
  highlightRange = null,
  onUserEditStart,
}: DraftSectionEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selectionRafRef = useRef<number | null>(null);
  const programmaticSelectionRef = useRef(false);
  const frozenSelectionRef = useRef<TextSelection | null>(null);
  const appliedHighlightKeyRef = useRef<string | null>(null);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [textareaFocused, setTextareaFocused] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<"selection" | "section">("selection");
  const [instruction, setInstruction] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revisionCompare, setRevisionCompare] = useState<{
    before: string;
    after: string;
    summary: string;
    instruction: string;
  } | null>(null);
  const [revisionDrawerOpen, setRevisionDrawerOpen] = useState(false);

  useEffect(() => {
    setSelection(null);
    frozenSelectionRef.current = null;
    setDialogOpen(false);
    setInstruction("");
    setError(null);
    setRevisionCompare(null);
    setRevisionDrawerOpen(false);
    appliedHighlightKeyRef.current = null;
  }, [section.id]);

  useEffect(() => {
    if (!highlightRange || !textareaRef.current || dialogOpen || isRunning) return;
    const ta = textareaRef.current;
    const { start, end } = highlightRange;
    if (start < 0 || end <= start || end > ta.value.length) return;

    const highlightKey = `${section.id}:${start}:${end}:${highlightRange.text}`;
    if (appliedHighlightKeyRef.current === highlightKey) return;
    appliedHighlightKeyRef.current = highlightKey;

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
  }, [dialogOpen, highlightRange, isRunning, section.id]);

  const clearSelection = useCallback(() => {
    if (selectionRafRef.current !== null) {
      window.cancelAnimationFrame(selectionRafRef.current);
      selectionRafRef.current = null;
    }
    setSelection(null);
  }, []);

  const captureSelection = useCallback(() => {
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

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (target.closest(".proposal-selection-revise-btn")) return;
      if (target === textareaRef.current) return;
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
      captureSelection();
    };
    ta?.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      ta?.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [section.id, captureSelection]);

  const openDialog = (mode: "selection" | "section") => {
    onUserEditStart?.();
    appliedHighlightKeyRef.current = null;
    if (mode === "selection" && selection) {
      frozenSelectionRef.current = { ...selection };
    } else {
      frozenSelectionRef.current = null;
    }
    setDialogMode(mode);
    setInstruction("");
    setError(null);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    if (isRunning) return;
    setDialogOpen(false);
    frozenSelectionRef.current = null;
    clearSelection();
  };

  const showRevisePill =
    Boolean(selection) && textareaFocused && !dialogOpen && !isRunning;

  const buildMessage = useCallback(() => {
    const trimmed = instruction.trim();
    if (dialogMode === "selection" && frozenSelectionRef.current) {
      return trimmed;
    }
    return trimmed;
  }, [dialogMode, instruction]);

  const applyRevision = useCallback(async () => {
    const trimmed = instruction.trim();
    if (!trimmed || isRunning) return;

    const activeSelection =
      dialogMode === "selection" ? frozenSelectionRef.current : null;
    if (dialogMode === "selection" && !activeSelection) {
      setError("Selection was lost — re-highlight the excerpt and try again.");
      return;
    }

    setIsRunning(true);
    setError(null);
    const contentBefore = value;

    try {
      const result = await improveProposalSection(
        rfpId,
        section.id,
        buildMessage(),
        activeSelection
          ? {
              selection: {
                start: activeSelection.start,
                end: activeSelection.end,
                text: activeSelection.text,
              },
            }
          : undefined
      );
      onSectionUpdated(result.draft, result.research);
      const contentAfter = result.section.content ?? contentBefore;
      const didChange = contentBefore !== contentAfter;
      if (didChange) {
        setRevisionCompare({
          before: contentBefore,
          after: contentAfter,
          summary: result.assistantMessage,
          instruction: trimmed,
        });
        setRevisionDrawerOpen(true);
      } else if (dialogMode === "selection") {
        setError(
          "No change was applied to the selected excerpt. Try a more specific instruction."
        );
        return;
      }
      setDialogOpen(false);
      frozenSelectionRef.current = null;
      setSelection(null);
      appliedHighlightKeyRef.current = null;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revision failed");
    } finally {
      setIsRunning(false);
    }
  }, [
    buildMessage,
    dialogMode,
    instruction,
    isRunning,
    onSectionUpdated,
    rfpId,
    section.id,
    value,
  ]);

  const dialogSelection = frozenSelectionRef.current;

  const selectionPreview =
    dialogMode === "selection" && dialogSelection
      ? dialogSelection.text
      : value.slice(0, 280) + (value.length > 280 ? "…" : "");

  return (
    <>
      <div className="proposal-draft-layout">
        <div className={`proposal-draft-main ${compact ? "is-compact" : ""}`}>
          <div className="proposal-draft-toolbar mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-zo-text-muted">
              Draft content
            </span>
            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
              {revisionCompare && !revisionDrawerOpen ? (
                <button
                  type="button"
                  onClick={() => setRevisionDrawerOpen(true)}
                  className="proposal-revision-reopen-btn"
                >
                  View what changed
                </button>
              ) : null}
              <button
                type="button"
                disabled={disabled || isRunning}
                onClick={() => openDialog("section")}
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

          {!compact ? (
            <p className="proposal-draft-hint mb-2 text-[11px] text-zo-text-muted">
              Highlight text — a <strong>Revise content</strong> button appears on the selection.
            </p>
          ) : null}

          <div className="proposal-draft-textarea-shell">
            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => {
                onUserEditStart?.();
                appliedHighlightKeyRef.current = null;
                onChange(e.target.value);
                setSelection(null);
                setRevisionCompare(null);
                setRevisionDrawerOpen(false);
              }}
              onSelect={captureSelection}
              onMouseUp={captureSelection}
              onKeyUp={captureSelection}
              onFocus={() => setTextareaFocused(true)}
              onBlur={() => setTextareaFocused(false)}
              disabled={disabled || isRunning}
              placeholder="Generate Sections 1–3 or run full proposal to auto-fill, or write manually…"
              className="proposal-draft-textarea zo-input w-full px-3 py-3 text-sm leading-[1.7] text-foreground outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
            />

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
                      openDialog("selection");
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

      {revisionCompare && revisionDrawerOpen && typeof document !== "undefined"
        ? createPortal(
            <>
              <button
                type="button"
                className="proposal-revision-drawer-backdrop"
                aria-label="Close revision compare"
                onClick={() => setRevisionDrawerOpen(false)}
              />
              <aside className="proposal-revision-drawer" aria-label="Revision changes">
                <SectionRevisionCompare
                  before={revisionCompare.before}
                  after={revisionCompare.after}
                  summary={revisionCompare.summary}
                  instruction={revisionCompare.instruction}
                  onDismiss={() => {
                    setRevisionDrawerOpen(false);
                    setRevisionCompare(null);
                  }}
                />
              </aside>
            </>,
            document.body
          )
        : null}

      {dialogOpen &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            className="proposal-revise-overlay"
            role="presentation"
            onClick={closeDialog}
          >
            <div
              className="proposal-revise-dialog"
              role="dialog"
              aria-labelledby="revise-dialog-title"
              aria-modal="true"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-start justify-between gap-3 border-b border-zo-border/70 px-5 py-4">
                <h3
                  id="revise-dialog-title"
                  className="font-heading text-lg font-bold tracking-tight text-foreground"
                >
                  {dialogMode === "selection" ? "Revise selection" : "Improve section"}
                </h3>
                <button
                  type="button"
                  onClick={closeDialog}
                  disabled={isRunning}
                  className="rounded-lg p-1.5 text-zo-text-muted transition-smooth hover:bg-zo-warm-gray/60 hover:text-foreground disabled:opacity-50"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div className="space-y-4 px-5 py-4">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-zo-text-muted">
                    {dialogMode === "selection" ? "Selected text" : "Section preview"}
                  </p>
                  <p className="proposal-revise-preview mt-2 text-sm leading-relaxed text-zo-text-secondary">
                    &ldquo;{selectionPreview}&rdquo;
                  </p>
                </div>

                <label className="block">
                  <span className="text-sm font-semibold text-foreground">
                    What should change?
                  </span>
                  <textarea
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    disabled={isRunning}
                    rows={4}
                    autoFocus
                    placeholder="e.g. shorten this paragraph, add a local reference, make the tone warmer, resolve the VERIFY tag from KB…"
                    className="proposal-revise-input zo-input mt-2 w-full px-3 py-2.5 text-sm leading-relaxed outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                  />
                </label>

                {dialogMode === "section" ? (
                  <div className="flex flex-wrap gap-2">
                    {SECTION_PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        disabled={isRunning}
                        onClick={() => setInstruction(prompt)}
                        className="rounded-full border border-zo-border bg-white px-3 py-1.5 text-left text-[11px] leading-snug text-zo-text-secondary transition-smooth hover:border-zo-orange hover:text-zo-orange disabled:opacity-50"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                ) : null}

                {error ? (
                  <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-zo-error">
                    {error}
                  </p>
                ) : null}
              </div>

              <div className="flex items-center justify-end gap-2 border-t border-zo-border/70 px-5 py-4">
                <button
                  type="button"
                  onClick={closeDialog}
                  disabled={isRunning}
                  className="zo-btn secondary !py-2.5 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void applyRevision()}
                  disabled={isRunning || !instruction.trim()}
                  className="proposal-revise-apply-btn disabled:opacity-50"
                >
                  {isRunning ? "Applying…" : "Apply revision"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}
    </>
  );
}
