"use client";

import { createPortal } from "react-dom";

export type PacketPlacePreviewMove = {
  kind?: string;
  heading?: string;
  fromTitle?: string;
  toTitle?: string;
  summary?: string;
};

export type PacketPlacePreview = {
  issues?: string[];
  moves?: PacketPlacePreviewMove[];
  humanGaps?: string[];
  stubTitles?: string[];
  plannedMoves?: number;
  summary?: string;
  nothingToMove?: boolean;
};

type PacketPlacePreviewModalProps = {
  open: boolean;
  loading: boolean;
  error: string | null;
  preview: PacketPlacePreview | null;
  applying?: boolean;
  onClose: () => void;
  onApply: () => void;
  onFixSectionOrder?: () => void;
};

export function PacketPlacePreviewModal({
  open,
  loading,
  error,
  preview,
  applying,
  onClose,
  onApply,
  onFixSectionOrder,
}: PacketPlacePreviewModalProps) {
  if (!open || typeof document === "undefined") return null;

  const moves = preview?.moves ?? [];
  const issues = (preview?.issues ?? []).filter(
    (line) => !/no misplaced blocks found/i.test(line)
  );
  const stubs = preview?.stubTitles ?? [];
  const nothingToMove =
    Boolean(preview?.nothingToMove) ||
    (!loading && !error && preview != null && moves.length === 0);
  const canApply = !loading && !error && !applying && moves.length > 0;

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="packet-place-preview-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/35 backdrop-blur-[2px]"
        aria-label="Close"
        onClick={onClose}
        disabled={applying}
      />
      <div className="relative z-10 flex max-h-[min(90vh,720px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-zo-border bg-white shadow-[0_24px_64px_rgba(15,23,42,0.18)]">
        <div className="flex items-start justify-between gap-4 border-b border-zo-border px-5 py-4">
          <div>
            <p
              id="packet-place-preview-title"
              className="text-lg font-semibold text-foreground"
            >
              {loading
                ? "Checking your proposal…"
                : nothingToMove
                  ? "Your text is already in the right places"
                  : "We found text in the wrong place"}
            </p>
            {!loading ? (
              <p className="mt-1 text-sm text-zo-text-secondary">
                {nothingToMove
                  ? "No cut-and-paste needed. You can close this."
                  : "Review the list below. We’ll only move text if you say yes — we won’t rewrite it."}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={applying}
            className="rounded-lg p-1 text-zo-text-muted hover:bg-zo-border/40 disabled:opacity-40"
            aria-label="Close"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <div className="custom-scrollbar flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <p className="text-sm text-zo-text-secondary">
              Looking for paragraphs that belong under a different heading on
              the left…
            </p>
          ) : null}
          {error ? (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          ) : null}

          {!loading && !error && preview && nothingToMove ? (
            <div className="space-y-4 text-sm leading-relaxed text-zo-text">
              <p>
                Think of the left side as a table of contents. We checked
                whether any <strong>paragraphs</strong> are under the wrong
                heading. They look fine.
              </p>
              <p className="rounded-xl border border-zo-border/80 bg-zo-bg-muted/30 px-4 py-3">
                <span className="font-semibold">Only one question for you:</span>
                <br />
                Does the <strong>order of names on the left</strong> match what
                the RFP asks for (first, second, third…)?
              </p>
              <ul className="list-none space-y-2">
                <li className="rounded-lg border border-emerald-200/80 bg-emerald-50/80 px-3 py-2 text-emerald-950">
                  <strong>Yes</strong> — you’re done. Click Close.
                </li>
                <li className="rounded-lg border border-amber-200/80 bg-amber-50/80 px-3 py-2 text-amber-950">
                  <strong>No</strong> — click “Reorder the left list” and we’ll
                  fix that sequence (not the paragraph text).
                </li>
              </ul>
              {stubs.length > 0 ? (
                <div className="rounded-xl border border-zo-border px-4 py-3">
                  <p className="font-semibold">Empty headings still need writing</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-zo-text-secondary">
                    {stubs.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {(preview.humanGaps?.length ?? 0) > 0 ? (
                <div className="rounded-xl border border-zo-border px-4 py-3">
                  <p className="font-semibold">Notes</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-zo-text-secondary">
                    {preview.humanGaps!.map((g) => (
                      <li key={g}>{g}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}

          {!loading && !error && preview && !nothingToMove ? (
            <div className="space-y-4">
              {issues.length > 0 ? (
                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-zo-text-muted">
                    What’s wrong
                  </h3>
                  <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-snug text-zo-text">
                    {issues.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </section>
              ) : null}

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zo-text-muted">
                  What we’ll move if you approve
                </h3>
                <ul className="mt-2 space-y-2">
                  {moves.map((m) => (
                    <li
                      key={
                        m.summary ?? `${m.fromTitle}-${m.toTitle}-${m.heading}`
                      }
                      className="rounded-lg border border-sky-200/80 bg-sky-50/80 px-3 py-2 text-sm text-sky-950"
                    >
                      <p className="font-medium">
                        {m.kind === "tab"
                          ? `Reorder “${m.heading ?? "section"}” on the left list`
                          : `Move “${m.heading ?? "this part"}” from “${m.fromTitle}” to “${m.toTitle}”`}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>

              {stubs.length > 0 ? (
                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800">
                    Still empty (you’ll write these later)
                  </h3>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-950">
                    {stubs.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-zo-border px-5 py-3">
          {nothingToMove ? (
            <>
              {onFixSectionOrder ? (
                <button
                  type="button"
                  className="zo-btn secondary !py-2 !px-3 !text-sm"
                  disabled={applying}
                  onClick={() => {
                    onClose();
                    onFixSectionOrder();
                  }}
                >
                  Reorder the left list
                </button>
              ) : null}
              <button
                type="button"
                className="zo-btn !py-2 !px-3 !text-sm"
                onClick={onClose}
                disabled={applying}
              >
                Close
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="zo-btn secondary !py-2 !px-3 !text-sm"
                onClick={onClose}
                disabled={applying}
              >
                Don’t move anything
              </button>
              <button
                type="button"
                className="zo-btn !py-2 !px-3 !text-sm disabled:opacity-40"
                disabled={!canApply}
                onClick={onApply}
              >
                {applying
                  ? "Moving…"
                  : `Yes, move ${moves.length} item${moves.length === 1 ? "" : "s"}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
