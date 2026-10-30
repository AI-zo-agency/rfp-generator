export interface SyncCounts {
  rfpsFound: number;
  rfpsCreated: number;
  rfpsSkipped: number;
  pdfsDownloaded: number;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * Plain-language outcome of a finished JustWin sync.
 *
 * `tab` and `scope` are optional: a sync picked back up after its window was
 * closed (or after a page change) no longer knows which tab or date was asked
 * for, so the message simply leaves those words out.
 */
export function describeSyncResult(
  counts: SyncCounts,
  opts: { tab?: string; scope?: string } = {}
): string {
  const { rfpsFound, rfpsCreated, rfpsSkipped, pdfsDownloaded } = counts;
  const tab = opts.tab ? `${opts.tab} ` : "";
  const where = opts.scope ? ` ${opts.scope}` : "";

  if (rfpsFound === 0) {
    return `No ${tab}leads found${where}.`;
  }
  if (rfpsCreated === 0 && rfpsSkipped > 0) {
    return (
      `No new RFPs${where} — ${rfpsSkipped} already in ZO were skipped` +
      (pdfsDownloaded > 0
        ? ` (${plural(pdfsDownloaded, "missing PDF")} backfilled).`
        : ".")
    );
  }
  if (rfpsSkipped > 0) {
    return (
      `Added ${plural(rfpsCreated, "new RFP")}${where}` +
      ` (${rfpsSkipped} already synced skipped` +
      `, ${plural(pdfsDownloaded, "PDF")} downloaded).`
    );
  }
  return (
    `Synced ${rfpsCreated} ${tab}${rfpsCreated === 1 ? "RFP" : "RFPs"}${where}` +
    ` (${plural(pdfsDownloaded, "PDF")} downloaded).`
  );
}
