export type DiffHunkType = "equal" | "add" | "remove" | "change";

export interface DiffHunk {
  type: DiffHunkType;
  before?: string;
  after?: string;
}

/** Paragraph-level hunks for revision compare UI. */
export function computeTextHunks(before: string, after: string): DiffHunk[] {
  if (before === after) return [];

  const beforeParas = splitParagraphs(before);
  const afterParas = splitParagraphs(after);
  const hunks: DiffHunk[] = [];

  let bi = 0;
  let ai = 0;

  while (bi < beforeParas.length || ai < afterParas.length) {
    if (
      bi < beforeParas.length &&
      ai < afterParas.length &&
      normalize(beforeParas[bi]) === normalize(afterParas[ai])
    ) {
      bi += 1;
      ai += 1;
      continue;
    }

    const startBi = bi;
    const startAi = ai;

    while (
      bi < beforeParas.length &&
      ai < afterParas.length &&
      normalize(beforeParas[bi]) !== normalize(afterParas[ai])
    ) {
      bi += 1;
      ai += 1;
    }

    if (bi === startBi && ai === startAi) {
      if (bi < beforeParas.length && ai >= afterParas.length) {
        hunks.push({ type: "remove", before: beforeParas[bi] });
        bi += 1;
      } else if (ai < afterParas.length && bi >= beforeParas.length) {
        hunks.push({ type: "add", after: afterParas[ai] });
        ai += 1;
      } else if (bi < beforeParas.length && ai < afterParas.length) {
        hunks.push({
          type: "change",
          before: beforeParas[bi],
          after: afterParas[ai],
        });
        bi += 1;
        ai += 1;
      } else {
        break;
      }
      continue;
    }

    const removed = beforeParas.slice(startBi, bi).join("\n\n").trim();
    const added = afterParas.slice(startAi, ai).join("\n\n").trim();
    if (removed && added) {
      hunks.push({ type: "change", before: removed, after: added });
    } else if (removed) {
      hunks.push({ type: "remove", before: removed });
    } else if (added) {
      hunks.push({ type: "add", after: added });
    }
  }

  if (hunks.length === 0 && before.trim() !== after.trim()) {
    const region = findChangedRegion(before, after);
    if (region.removed || region.added) {
      hunks.push({
        type: region.removed && region.added ? "change" : region.removed ? "remove" : "add",
        before: region.removed || undefined,
        after: region.added || undefined,
      });
    }
  }

  return hunks;
}

function splitParagraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
}

function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function findChangedRegion(before: string, after: string) {
  let start = 0;
  const minLen = Math.min(before.length, after.length);
  while (start < minLen && before[start] === after[start]) start += 1;

  let endBefore = before.length;
  let endAfter = after.length;
  while (
    endBefore > start &&
    endAfter > start &&
    before[endBefore - 1] === after[endAfter - 1]
  ) {
    endBefore -= 1;
    endAfter -= 1;
  }

  return {
    removed: before.slice(start, endBefore).trim(),
    added: after.slice(start, endAfter).trim(),
  };
}

export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

export type InlineDiffHighlight = "remove" | "add";

export interface InlineDiffSegment {
  text: string;
  highlight?: InlineDiffHighlight;
}

/** Prefix / changed middle / suffix for side-by-side inline highlights. */
export function inlineDiffSegments(
  text: string,
  other: string,
  side: "before" | "after"
): InlineDiffSegment[] {
  if (!text.trim()) return [];
  if (!other.trim()) {
    return [{ text, highlight: side === "before" ? "remove" : "add" }];
  }

  let start = 0;
  const minLen = Math.min(text.length, other.length);
  while (start < minLen && text[start] === other[start]) start += 1;

  let endText = text.length;
  let endOther = other.length;
  while (
    endText > start &&
    endOther > start &&
    text[endText - 1] === other[endOther - 1]
  ) {
    endText -= 1;
    endOther -= 1;
  }

  const segments: InlineDiffSegment[] = [];
  if (start > 0) segments.push({ text: text.slice(0, start) });
  const mid = text.slice(start, endText);
  if (mid) {
    segments.push({
      text: mid,
      highlight: side === "before" ? "remove" : "add",
    });
  }
  const tail = text.slice(endText);
  if (tail) segments.push({ text: tail });
  return segments.length ? segments : [{ text }];
}
