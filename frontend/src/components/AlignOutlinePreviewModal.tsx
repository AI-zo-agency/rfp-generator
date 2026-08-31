"use client";

import { useMemo } from "react";
import { createPortal } from "react-dom";

export type AlignOutlinePreview = {
  currentTitles?: string[];
  proposedTitles?: string[];
  rfpNeededTitles?: string[];
  changes?: string[];
  addedTitles?: string[];
  nothingToChange?: boolean;
  summary?: string;
  humanDecisionGaps?: string[];
};

type AlignOutlinePreviewModalProps = {
  open: boolean;
  loading: boolean;
  error: string | null;
  preview: AlignOutlinePreview | null;
  applying?: boolean;
  onClose: () => void;
  onApply: () => void;
};

type DiffKind = "same" | "moved" | "added" | "removed";

type DiffRow = {
  key: string;
  kind: DiffKind;
  title: string;
  fromIndex?: number;
  toIndex?: number;
};

function normTitle(t: string): string {
  return t.trim().toLowerCase();
}

/** True for internal Complete-Scan style notes — never show in Align preview. */
export function isInternalAlignNote(text: string): boolean {
  const t = text.toLowerCase();
  return (
    t.includes("re-run scan") ||
    t.includes("with llm") ||
    t.includes("to reframe") ||
    t.includes("missing rfp outline") ||
    t.includes("restore snapshot")
  );
}

/** Build scannable move/add/remove rows. */
export function buildAlignDiffRows(
  current: string[],
  proposed: string[]
): DiffRow[] {
  const curIndex = new Map<string, number>();
  current.forEach((t, i) => {
    const k = normTitle(t);
    if (k && !curIndex.has(k)) curIndex.set(k, i);
  });
  const propIndex = new Map<string, number>();
  proposed.forEach((t, i) => {
    const k = normTitle(t);
    if (k && !propIndex.has(k)) propIndex.set(k, i);
  });

  const rows: DiffRow[] = [];
  proposed.forEach((title, toIndex) => {
    const k = normTitle(title);
    const fromIndex = curIndex.get(k);
    if (fromIndex === undefined) {
      rows.push({
        key: `add-${toIndex}-${k}`,
        kind: "added",
        title,
        toIndex,
      });
      return;
    }
    if (fromIndex !== toIndex) {
      rows.push({
        key: `move-${toIndex}-${k}`,
        kind: "moved",
        title,
        fromIndex,
        toIndex,
      });
    }
  });
  current.forEach((title, fromIndex) => {
    const k = normTitle(title);
    if (!propIndex.has(k)) {
      rows.push({
        key: `rm-${fromIndex}-${k}`,
        kind: "removed",
        title,
        fromIndex,
      });
    }
  });
  return rows;
}

function shortTitle(title: string, max = 72): string {
  const t = title.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function friendlyPreviewError(error: string): string {
  const e = error.trim();
  if (/fetch failed|failed to fetch|networkerror/i.test(e)) {
    return "Couldn’t reach the server. Make sure the backend is running, then try again.";
  }
  return e;
}

export function AlignOutlinePreviewModal({
  open,
  loading,
  error,
  preview,
  applying,
  onClose,
  onApply,
}: AlignOutlinePreviewModalProps) {
  const current = preview?.currentTitles ?? [];
  const proposed = preview?.proposedTitles ?? [];
  const nothing = Boolean(preview?.nothingToChange);
  const canApply =
    !loading && !error && !applying && !nothing && proposed.length > 0;

  const diffRows = useMemo(
    () => buildAlignDiffRows(current, proposed),
    [current, proposed]
  );
  const added = useMemo(
    () => diffRows.filter((r) => r.kind === "added"),
    [diffRows]
  );
  const removed = useMemo(
    () => diffRows.filter((r) => r.kind === "removed"),
    [diffRows]
  );
  const moved = useMemo(
    () => diffRows.filter((r) => r.kind === "moved"),
    [diffRows]
  );

  const cascadeOnly =
    added.length > 0 &&
    moved.length > 0 &&
    moved.every(
      (m) =>
        m.fromIndex != null &&
        m.toIndex != null &&
        m.toIndex - m.fromIndex === added.length
    );

  const clientNotes = useMemo(
    () =>
      (preview?.humanDecisionGaps ?? []).filter(
        (n) => n.trim() && !isInternalAlignNote(n)
      ),
    [preview?.humanDecisionGaps]
  );

  const addedSet = useMemo(
    () => new Set(added.map((r) => normTitle(r.title))),
    [added]
  );
  const removedSet = useMemo(
    () => new Set(removed.map((r) => normTitle(r.title))),
    [removed]
  );
  const movedSet = useMemo(
    () => new Set(moved.map((r) => normTitle(r.title))),
    [moved]
  );
  const rfpNeeds = preview?.rfpNeededTitles?.length
    ? preview.rfpNeededTitles
    : proposed;

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="align-outline-preview-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/35 backdrop-blur-[2px]"
        aria-label="Close"
        onClick={onClose}
        disabled={applying}
      />
      <div className="relative z-10 flex max-h-[min(92vh,44rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-zo-border bg-white shadow-[0_24px_64px_rgba(15,23,42,0.18)]">
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-zo-border px-5 py-4">
          <div className="min-w-0">
            <h2
              id="align-outline-preview-title"
              className="text-lg font-bold tracking-tight text-foreground"
            >
              {loading
                ? "Checking your table of contents…"
                : nothing
                  ? "Nothing to change"
                  : "Compare your list to the RFP"}
            </h2>
            <p className="mt-1 text-sm leading-snug text-zo-text-secondary">
              {loading
                ? "Seeing how your headings compare to what the RFP asks for."
                : nothing
                  ? "Your list already matches the RFP. You can close this."
                  : "Left = what you have now. Right = what we’ll set. Paragraph text is not rewritten."}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={applying}
            className="shrink-0 rounded-lg p-1 text-zo-text-muted hover:bg-zo-border/40 disabled:opacity-40"
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
        </header>

        <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <p className="text-sm text-zo-text-secondary">One moment…</p>
          ) : null}
          {error ? (
            <p
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
              role="alert"
            >
              {friendlyPreviewError(error)}
            </p>
          ) : null}

          {!loading && !error && preview && nothing ? (
            <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
              You’re already aligned. No action needed.
            </p>
          ) : null}

          {!loading && !error && preview && !nothing ? (
            <div className="space-y-5">
              {/* 1. Side-by-side comparison FIRST */}
              <section aria-label="Side-by-side comparison">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-zo-border bg-zo-bg/30 p-3">
                    <p className="mb-2 text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                      What you have now
                    </p>
                    <ol className="max-h-[min(42vh,20rem)] list-none space-y-0.5 overflow-y-auto pr-1 text-sm leading-snug text-zo-text">
                      {current.map((title, i) => {
                        const k = normTitle(title);
                        const gone = removedSet.has(k);
                        const shifted = movedSet.has(k);
                        const isDiff = gone || shifted;
                        return (
                          <li
                            key={`cur-${i}-${k}`}
                            className={`flex gap-2 rounded-md px-1.5 py-1 ${
                              isDiff
                                ? "bg-amber-200/90 text-amber-950 ring-1 ring-amber-300/80"
                                : ""
                            } ${gone ? "line-through decoration-amber-700/50" : ""}`}
                          >
                            <span
                              className="w-6 shrink-0 text-right text-xs font-semibold text-zo-text-muted"
                              aria-hidden
                            >
                              {i + 1}
                            </span>
                            <span className="min-w-0">{shortTitle(title, 100)}</span>
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                  <div className="rounded-xl border border-emerald-200/80 bg-emerald-50/40 p-3">
                    <p className="mb-2 text-xs font-bold uppercase tracking-wide text-emerald-900/80">
                      What the RFP needs (after update)
                    </p>
                    <ol className="max-h-[min(42vh,20rem)] list-none space-y-0.5 overflow-y-auto pr-1 text-sm leading-snug text-zo-text">
                      {proposed.map((title, i) => {
                        const k = normTitle(title);
                        const isNew = addedSet.has(k);
                        const shifted = movedSet.has(k);
                        const isDiff = isNew || shifted;
                        return (
                          <li
                            key={`prop-${i}-${k}`}
                            className={`flex gap-2 rounded-md px-1.5 py-1 ${
                              isDiff
                                ? "bg-amber-200/90 font-medium text-amber-950 ring-1 ring-amber-300/80"
                                : ""
                            }`}
                          >
                            <span
                              className="w-6 shrink-0 text-right text-xs font-semibold text-zo-text-muted"
                              aria-hidden
                            >
                              {i + 1}
                            </span>
                            <span className="min-w-0">{shortTitle(title, 100)}</span>
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                </div>
              </section>

              {/* 2. Changes + why LAST */}
              <section
                className="space-y-3 border-t border-zo-border pt-4"
                aria-label="What changes and why"
              >
                <h3 className="text-sm font-bold text-zo-text">
                  What changes — and why
                </h3>
                <p className="text-sm leading-snug text-zo-text-secondary">
                  The RFP asks for this heading order. We only adjust the left
                  list to match it.
                </p>

                {added.length > 0 ? (
                  <div className="rounded-xl border border-emerald-200/90 bg-emerald-50/70 px-4 py-3">
                    <p className="text-[0.8125rem] font-bold text-emerald-950">
                      Add blank headings the RFP asks for
                    </p>
                    <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-emerald-950">
                      {added.map((r) => (
                        <li key={r.key}>{shortTitle(r.title)}</li>
                      ))}
                    </ul>
                    <p className="mt-1.5 text-xs text-emerald-900/80">
                      Why: they’re required by the RFP but missing on your list.
                      Empty until you write them.
                    </p>
                  </div>
                ) : null}

                {removed.length > 0 ? (
                  <div className="rounded-xl border border-red-200/80 bg-red-50/70 px-4 py-3">
                    <p className="text-[0.8125rem] font-bold text-red-950">
                      Take these off the left list
                    </p>
                    <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-red-950">
                      {removed.map((r) => (
                        <li key={r.key}>{shortTitle(r.title)}</li>
                      ))}
                    </ul>
                    <p className="mt-1.5 text-xs text-red-900/80">
                      Why: they aren’t in the RFP’s required order. A backup is
                      saved first so you can Restore.
                    </p>
                  </div>
                ) : null}

                {moved.length > 0 ? (
                  <div className="rounded-xl border border-sky-200/90 bg-sky-50/70 px-4 py-3">
                    <p className="text-[0.8125rem] font-bold text-sky-950">
                      {cascadeOnly
                        ? "Some headings shift down"
                        : "Some headings move in the list"}
                    </p>
                    <p className="mt-1.5 text-xs leading-snug text-sky-900/85">
                      {cascadeOnly
                        ? "Why: inserting a new RFP heading above them nudges the ones below — same titles, new order."
                        : "Why: the RFP wants these in a different position than you have now."}
                    </p>
                    {!cascadeOnly ? (
                      <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-sky-950">
                        {moved.slice(0, 10).map((r) => (
                          <li key={r.key}>{shortTitle(r.title)}</li>
                        ))}
                        {moved.length > 10 ? (
                          <li>+{moved.length - 10} more</li>
                        ) : null}
                      </ul>
                    ) : null}
                  </div>
                ) : null}

                {clientNotes.length > 0 ? (
                  <div className="rounded-xl border border-amber-200/80 bg-amber-50/80 px-4 py-3">
                    <p className="text-[0.8125rem] font-bold text-amber-950">
                      Also worth knowing
                    </p>
                    <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-amber-950">
                      {clientNotes.map((g) => (
                        <li key={g}>{g}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {rfpNeeds.length > 0 ? (
                  <details className="rounded-xl border border-zo-border px-3 py-2">
                    <summary className="cursor-pointer text-sm font-semibold text-zo-text">
                      Full RFP heading order ({rfpNeeds.length})
                    </summary>
                    <ol className="mt-2 max-h-40 list-decimal space-y-1 overflow-y-auto pl-5 text-xs leading-snug text-zo-text-secondary">
                      {rfpNeeds.map((title, i) => (
                        <li key={`need-${i}-${normTitle(title)}`}>
                          {shortTitle(title, 100)}
                        </li>
                      ))}
                    </ol>
                  </details>
                ) : null}
              </section>
            </div>
          ) : null}
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-zo-border bg-zo-bg/40 px-5 py-3">
          <button
            type="button"
            className="zo-btn secondary !py-2 !px-3 !text-sm"
            onClick={onClose}
            disabled={applying}
          >
            {nothing ? "Close" : "Keep my list as-is"}
          </button>
          {!nothing ? (
            <button
              type="button"
              className="zo-btn !py-2 !px-3 !text-sm disabled:opacity-40"
              disabled={!canApply}
              onClick={onApply}
            >
              {applying ? "Updating…" : "Yes, update the list"}
            </button>
          ) : null}
        </footer>
      </div>
    </div>,
    document.body
  );
}
