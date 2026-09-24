/** Derive Fit track labels and bid-scope lock readiness for multi-lot RFPs. */

export function availableTracksFromAnalysis(
  analysis:
    | {
        availableTracks?: string[];
        capabilityMatrix?: Array<{ track?: string }>;
      }
    | null
    | undefined,
): string[] {
  if (analysis?.availableTracks?.length) {
    return [...analysis.availableTracks];
  }
  const seen = new Set<string>();
  const out: string[] = [];
  for (const row of analysis?.capabilityMatrix ?? []) {
    const t = (row.track ?? "").trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}

export function bidScopeNeedsLock(tracks: string[]): boolean {
  return tracks.length >= 2;
}

export function bidScopeIsReady(
  tracks: string[],
  lockedAt: string | null | undefined,
  selected: string[] | null | undefined,
): boolean {
  if (!bidScopeNeedsLock(tracks)) return true;
  return Boolean(lockedAt && selected && selected.length > 0);
}
