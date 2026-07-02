"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { ManualFillFlag, ManualFillFlagKind } from "@/lib/proposal-manual-flags";

interface ProposalManualFlagsPanelProps {
  open: boolean;
  flags: ManualFillFlag[];
  summary: string;
  activeSectionId?: string | null;
  onJumpToFlag: (flag: ManualFillFlag) => void;
  onClose: () => void;
  onResolveAll?: () => void;
  isResolving?: boolean;
  resolveNotice?: string | null;
  resolveError?: string | null;
}

function kindLabel(kind: ManualFillFlagKind): string {
  if (kind === "manual_fill") return "MANUAL FILL";
  if (kind === "verify") return "VERIFY";
  if (kind === "placeholder") return "PLACEHOLDER";
  if (kind === "compliance") return "COMPLIANCE";
  if (kind === "budget") return "BUDGET";
  if (kind === "consistency") return "CONSISTENCY";
  return "FLAG";
}

function kindStyles(kind: ManualFillFlagKind): string {
  if (kind === "manual_fill") return "bg-violet-100 text-violet-900";
  if (kind === "verify") return "bg-red-100 text-red-800";
  if (kind === "placeholder") return "bg-amber-100 text-amber-900";
  if (kind === "compliance") return "bg-orange-100 text-orange-900";
  if (kind === "budget") return "bg-violet-100 text-violet-900";
  if (kind === "consistency") return "bg-sky-100 text-sky-900";
  return "bg-zo-warm-gray/70 text-zo-text-secondary";
}

function truncateTag(tag: string, max = 96): string {
  const inner = tag.replace(/^\[|\]$/g, "");
  if (inner.length <= max) return inner;
  return `${inner.slice(0, max - 1)}…`;
}

export function ProposalManualFlagsPanel({
  open,
  flags,
  summary,
  activeSectionId,
  onJumpToFlag,
  onClose,
  onResolveAll,
  isResolving = false,
  resolveNotice,
  resolveError,
}: ProposalManualFlagsPanelProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  const bySection = new Map<string, { title: string; items: ManualFillFlag[] }>();
  for (const flag of flags) {
    const entry = bySection.get(flag.sectionId) ?? {
      title: flag.sectionTitle,
      items: [],
    };
    entry.items.push(flag);
    bySection.set(flag.sectionId, entry);
  }

  return createPortal(
    <>
      <button
        type="button"
        className="proposal-manual-flags-backdrop"
        aria-label="Close manual fill-ins panel"
        onClick={onClose}
      />
      <aside
        className="proposal-manual-flags-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="manual-fill-ins-title"
      >
        <header className="proposal-manual-flags-drawer-header">
          <div className="min-w-0 pr-2">
            <p id="manual-fill-ins-title" className="text-sm font-bold text-amber-950">
              Submission gaps
            </p>
            <p className="mt-0.5 text-xs leading-relaxed text-amber-900/90">
              {summary}
            </p>
            <p className="mt-1 text-[11px] text-amber-800/75">
              Click a row to jump and highlight the exact passage in Content. Resolve queries
              Supermemory only (no AI rewrite) — fills tags when KB has facts, else MANUAL FILL.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="proposal-manual-flags-drawer-close"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        {flags.length === 0 ? (
          <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-emerald-800">
            {summary}
          </div>
        ) : (
          <ul className="proposal-manual-flags-drawer-list custom-scrollbar">
            {[...bySection.entries()].map(([sectionId, { title, items }]) => (
              <li key={sectionId} className="proposal-manual-flags-drawer-group">
                <button
                  type="button"
                  onClick={() => onJumpToFlag(items[0]!)}
                  className={`proposal-manual-flags-section-btn ${
                    activeSectionId === sectionId ? "is-active" : ""
                  }`}
                >
                  <span className="line-clamp-2 text-left">{title}</span>
                  <span className="shrink-0 rounded-full bg-amber-200/80 px-2 py-0.5 text-[10px] font-bold text-amber-950">
                    {items.length}
                  </span>
                </button>
                <ul className="space-y-1 px-2 pb-2">
                  {items.map((flag, index) => (
                    <li key={`${sectionId}-${index}`}>
                      <button
                        type="button"
                        onClick={() => onJumpToFlag(flag)}
                        className={`proposal-manual-flags-item-btn ${
                          activeSectionId === sectionId ? "is-active" : ""
                        }`}
                      >
                        <span
                          className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${kindStyles(flag.kind)}`}
                        >
                          {kindLabel(flag.kind)}
                        </span>
                        <span className="min-w-0 break-words text-left">
                          {flag.owner ? (
                            <span className="mr-1 font-semibold text-violet-800">{flag.owner}:</span>
                          ) : null}
                          {truncateTag(flag.tag)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}

        {onResolveAll ? (
          <footer className="proposal-manual-flags-drawer-footer">
            {resolveError ? (
              <p className="mb-2 rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-[11px] leading-relaxed text-red-800">
                {resolveError}
              </p>
            ) : null}
            {resolveNotice && !resolveError ? (
              <p className="mb-2 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-2 text-[11px] leading-relaxed text-emerald-900">
                {resolveNotice}
              </p>
            ) : null}
            <button
              type="button"
              onClick={onResolveAll}
              disabled={isResolving || flags.length === 0}
              className="zo-btn w-full justify-center text-sm"
              title="Search KB for each gap, fill what we can, and assign Sonja/Ella MANUAL FILL tags for the rest"
            >
              {isResolving ? (
                <>
                  <span
                    className="h-4 w-4 animate-spin rounded-full border-2 border-zo-white/30 border-t-zo-white"
                    aria-hidden
                  />
                  Resolving gaps…
                </>
              ) : (
                `Resolve all gaps (${flags.length})`
              )}
            </button>
            <p className="mt-2 text-center text-[10px] leading-relaxed text-amber-900/70">
              KB search + fill · Sonja/Ella tags for anything still missing
            </p>
          </footer>
        ) : null}
      </aside>
    </>,
    document.body
  );
}
