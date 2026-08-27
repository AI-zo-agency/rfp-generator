/**
 * Toggle markdown emphasis markers around a source range.
 * Handles selections that sit inside an existing run (common when highlighting
 * in the formatted preview, where `**`/`_` are not visible).
 */

export function toggleWrapMarkers(
  value: string,
  start: number,
  end: number,
  marker: string
): { next: string; rangeStart: number; rangeEnd: number } {
  let s = start;
  let e = end;
  if (s > e) [s, e] = [e, s];
  s = Math.max(0, Math.min(s, value.length));
  e = Math.max(0, Math.min(e, value.length));
  const m = marker.length;

  const envelope = findMarkerEnvelope(value, s, e, marker);
  if (envelope) {
    const inner = value.slice(envelope.open + m, envelope.close - m);
    const next =
      value.slice(0, envelope.open) + inner + value.slice(envelope.close);
    return {
      next,
      rangeStart: envelope.open,
      rangeEnd: envelope.open + inner.length,
    };
  }

  // Accidental ****text**** (double-wrap) → strip all the way to plain text.
  const selected = value.slice(s, e);
  const double = marker + marker;
  if (
    selected.startsWith(double) &&
    selected.endsWith(double) &&
    selected.length > double.length * 2
  ) {
    const inner = selected.slice(double.length, -double.length);
    const next = value.slice(0, s) + inner + value.slice(e);
    return { next, rangeStart: s, rangeEnd: s + inner.length };
  }

  const placeholder = selected || "text";
  const wrapped = `${marker}${placeholder}${marker}`;
  const next = value.slice(0, s) + wrapped + value.slice(e);
  return { next, rangeStart: s, rangeEnd: s + wrapped.length };
}

/** True when the range is already emphasized with this marker. */
export function selectionHasMarker(
  value: string,
  start: number,
  end: number,
  marker: string
): boolean {
  let s = start;
  let e = end;
  if (s > e) [s, e] = [e, s];
  return findMarkerEnvelope(value, s, e, marker) != null;
}

function findMarkerEnvelope(
  value: string,
  start: number,
  end: number,
  marker: string
): { open: number; close: number } | null {
  const m = marker.length;
  if (m === 0 || end < start) return null;

  // Markers sit just outside the selection.
  if (
    start >= m &&
    end + m <= value.length &&
    value.slice(start - m, start) === marker &&
    value.slice(end, end + m) === marker
  ) {
    return { open: start - m, close: end + m };
  }

  // Selection includes the markers (preview map often expands to them).
  if (
    end - start > m * 2 &&
    value.slice(start, start + m) === marker &&
    value.slice(end - m, end) === marker
  ) {
    const inner = value.slice(start + m, end - m);
    if (!inner.includes(marker)) {
      return { open: start, close: end };
    }
  }

  // Selection is inside a marked run — walk out to the nearest pair.
  const open = findOpenMarker(value, start, marker);
  if (open == null) return null;
  const close = findCloseMarker(value, Math.max(end, open + m), marker);
  if (close == null) return null;
  const inner = value.slice(open + m, close - m);
  if (inner.includes(marker)) return null;
  // Selection must sit within this run (not a distant coincidental pair).
  if (start < open || end > close) return null;
  if (start >= open + m && end <= close - m) {
    return { open, close };
  }
  if (start >= open && end <= close) {
    return { open, close };
  }
  return null;
}

function findOpenMarker(
  value: string,
  before: number,
  marker: string
): number | null {
  const m = marker.length;
  for (let i = before - m; i >= 0; i -= 1) {
    if (value.slice(i, i + m) === marker) {
      // Prefer the closest marker; skip a third * in *** by requiring
      // the char before open isn't the same marker char for ** runs.
      if (marker === "**" && i > 0 && value[i - 1] === "*") continue;
      return i;
    }
    if (value[i + m - 1] === "\n") {
      // Don't cross blank lines / new blocks.
      const at = i + m - 1;
      if (at > 0 && value[at - 1] === "\n") return null;
    }
  }
  return null;
}

function findCloseMarker(
  value: string,
  after: number,
  marker: string
): number | null {
  const m = marker.length;
  for (let i = after; i + m <= value.length; i += 1) {
    if (value.slice(i, i + m) === marker) {
      if (marker === "**" && i + m < value.length && value[i + m] === "*") {
        continue;
      }
      return i + m;
    }
    if (value[i] === "\n" && i + 1 < value.length && value[i + 1] === "\n") {
      return null;
    }
  }
  return null;
}
