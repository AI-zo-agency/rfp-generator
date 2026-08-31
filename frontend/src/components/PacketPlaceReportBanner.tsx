"use client";

export type PacketPlaceReport = {
  mode?: string;
  movedCount?: number;
  plannedMoves?: number;
  moveSummaries?: string[];
  skipped?: string[];
  humanGaps?: string[];
  stubTitles?: string[];
  summary?: string;
  changed?: boolean;
};

type PacketPlaceReportBannerProps = {
  report: PacketPlaceReport;
  onDismiss: () => void;
};

export function PacketPlaceReportBanner({
  report,
  onDismiss,
}: PacketPlaceReportBannerProps) {
  const moved = report.movedCount ?? 0;
  const planned = report.plannedMoves ?? moved;
  const moves = report.moveSummaries ?? [];
  const gaps = report.humanGaps ?? [];
  const stubs = report.stubTitles ?? [];
  const skipped = report.skipped ?? [];
  const flagCount = gaps.length + stubs.length + skipped.length;

  return (
    <div className="border-t border-sky-200/90 bg-sky-50 px-3 py-2.5 text-sky-950 md:px-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            Fix RFP order — move writing finished
          </p>
          <p className="mt-0.5 text-xs leading-snug text-sky-900/90">
            Applied {moved}
            {planned > 0 ? ` of ${planned} planned` : ""} move
            {moved === 1 ? "" : "s"} (text unchanged).{" "}
            {flagCount > 0
              ? `${flagCount} note${flagCount === 1 ? "" : "s"} for you to review.`
              : "No extra flags."}{" "}
            Undo: Restore “Before packet redistribute”.
          </p>
        </div>
        <button
          type="button"
          className="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium text-sky-800 hover:bg-sky-100"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          Dismiss
        </button>
      </div>

      {moves.length > 0 ? (
        <details className="mt-2 rounded-md border border-sky-200/80 bg-white/70 px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs font-semibold">
            What moved ({moves.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-snug">
            {moves.slice(0, 25).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="mt-2 text-xs text-sky-900/80">
          No blocks were moved — the planner found nothing to relocate (or
          headings did not match).
        </p>
      )}

      {gaps.length > 0 ? (
        <details className="mt-1.5 rounded-md border border-amber-200/90 bg-amber-50/80 px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs font-semibold text-amber-950">
            Needs your attention ({gaps.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-snug text-amber-950">
            {gaps.slice(0, 20).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      ) : null}

      {stubs.length > 0 ? (
        <details className="mt-1.5 rounded-md border border-amber-200/90 bg-amber-50/80 px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs font-semibold text-amber-950">
            Empty slots still to fill ({stubs.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-snug text-amber-950">
            {stubs.slice(0, 20).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      ) : null}

      {skipped.length > 0 ? (
        <details className="mt-1.5 rounded-md border border-zo-border/70 bg-white/60 px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs font-semibold text-zo-text">
            Skipped ({skipped.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-snug text-zo-text-secondary">
            {skipped.slice(0, 20).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
