/**
 * Maps a selection made in the rendered markdown preview back onto character
 * offsets in the raw markdown source.
 *
 * MarkdownReportBody strips markup before it reaches the DOM: `**bold**`
 * markers, heading/list/quote prefixes, table pipes and evidence citations all
 * disappear, and wrapped paragraph lines get joined with a space. So the string
 * `window.getSelection()` hands back never appears verbatim in the source, and
 * a plain `indexOf` misses any selection crossing one of those — which silently
 * killed the "Revise content" pill.
 *
 * We project the source into the same plain text the renderer produces while
 * recording, per projected character, the source index it came from. Matching
 * happens in projected space; the hit maps back to real offsets.
 */

export interface MarkdownProjection {
  text: string;
  /** `indices[i]` is the offset in the source that produced `text[i]`. */
  indices: number[];
}

export interface SourceRange {
  start: number;
  end: number;
}

import { humanizeGapTag, isInternalScanTag } from "./gap-tag-humanize";
const THEMATIC_BREAK = /^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/;
const CODE_FENCE = /^[ \t]*```/;
const REFERENCES_ONLY =
  /^[ \t]*\**[ \t]*References?[ \t]*\**[ \t]*:?[ \t]*\**[ \t]*(?:\[?[ \t]*E\d+(?:[ \t]*[,;][ \t]*E\d+)*[ \t]*\]?)?[ \t]*$/i;

/** The `| --- | :-- |` row under a table header, which renders as nothing. */
function isTableDelimiter(line: string): boolean {
  return line.includes("|") && line.includes("-") && /^[ \t|:-]+$/.test(line);
}

/**
 * Block prefixes the parser consumes (heading, bullet, ordered).
 * Deliberately no blockquote: MarkdownReportBody has no `>` block, so it
 * renders the marker as literal text and the projection must too.
 */
const LINE_PREFIX = /[ \t]*(?:#{1,6}[ \t]+|(?:[-*+]|\d{1,3}[.)])[ \t]+)/y;

/** Inline runs stripEvidenceCitations() removes before rendering. */
const CITATION = /\*{0,2}\[[ \t]*E\d+(?:[ \t]*[,;][ \t]*E\d+)*[ \t]*\]\*{0,2}/iy;
const PRICING_FLAG = /\[PRICING FLAG:[^\]]*\]/iy;
const HTML_COMMENT = /<!--[\s\S]*?-->/y;
const GAP_TAG =
  /\[(?:VERIFY|MANUAL FILL|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER)[^\]]*\]/iy;

function visibleGapProjection(tag: string): string {
  if (isInternalScanTag(tag)) return "";
  if (/^\[(?:VERIFY|MANUAL\s+FILL)/i.test(tag)) {
    const h = humanizeGapTag(tag);
    return h.detail ? `${h.title} — ${h.detail}` : h.title;
  }
  return tag;
}

/** Emphasis/code punctuation the renderer eats rather than displays. */
const MARKUP_CHARS = new Set(["*", "`", "~"]);

export function projectMarkdown(source: string): MarkdownProjection {
  const chars: string[] = [];
  const indices: number[] = [];

  const pushSpace = (at: number) => {
    if (chars.length === 0) return; // never lead with whitespace
    if (chars[chars.length - 1] === " ") return; // collapse runs
    chars.push(" ");
    indices.push(at);
  };

  // stripEvidenceCitations() eats the whitespace in front of a citation, so
  // "certifications [E4]." renders as "certifications." — drop the space we
  // already emitted or the projection gains a phantom gap.
  const dropPendingSpace = () => {
    if (chars[chars.length - 1] === " ") {
      chars.pop();
      indices.pop();
    }
  };

  let lineStart = 0;
  while (lineStart <= source.length) {
    let lineEnd = source.indexOf("\n", lineStart);
    if (lineEnd === -1) lineEnd = source.length;
    const line = source.slice(lineStart, lineEnd);

    const dropped =
      THEMATIC_BREAK.test(line) ||
      CODE_FENCE.test(line) ||
      REFERENCES_ONLY.test(line) ||
      isTableDelimiter(line);

    if (!dropped) {
      let i = 0;

      // Peel nested block prefixes, e.g. "> - item".
      for (;;) {
        LINE_PREFIX.lastIndex = i;
        const prefix = LINE_PREFIX.exec(line);
        if (!prefix || prefix[0].length === 0) break;
        i += prefix[0].length;
      }

      while (i < line.length) {
        CITATION.lastIndex = i;
        const citation = CITATION.exec(line);
        if (citation) {
          dropPendingSpace();
          i += citation[0].length;
          continue;
        }
        PRICING_FLAG.lastIndex = i;
        const flag = PRICING_FLAG.exec(line);
        if (flag) {
          dropPendingSpace();
          i += flag[0].length;
          continue;
        }
        HTML_COMMENT.lastIndex = i;
        const comment = HTML_COMMENT.exec(line);
        if (comment) {
          dropPendingSpace();
          i += comment[0].length;
          continue;
        }
        GAP_TAG.lastIndex = i;
        const gap = GAP_TAG.exec(line);
        if (gap) {
          const tag = gap[0];
          const visible = visibleGapProjection(tag);
          const tagStart = lineStart + i;
          const tagEnd = tagStart + tag.length - 1;
          for (let k = 0; k < visible.length; k += 1) {
            const ch = visible[k]!;
            const srcAt = k === visible.length - 1 ? tagEnd : tagStart;
            if (ch === " " || ch === "\t") {
              pushSpace(srcAt);
            } else {
              chars.push(ch);
              indices.push(srcAt);
            }
          }
          i += tag.length;
          continue;
        }

        const char = line[i]!;
        if (MARKUP_CHARS.has(char)) {
          i += 1;
          continue;
        }
        if (char === "|" || char === " " || char === "\t") {
          // Table cell borders read as a gap, same as the rendered <td> gap.
          pushSpace(lineStart + i);
          i += 1;
          continue;
        }
        chars.push(char);
        indices.push(lineStart + i);
        i += 1;
      }
    }

    // A line break is a gap between blocks — the renderer shows it as one.
    pushSpace(lineEnd);
    if (lineEnd === source.length) break;
    lineStart = lineEnd + 1;
  }

  while (chars.length > 0 && chars[chars.length - 1] === " ") {
    chars.pop();
    indices.pop();
  }

  return { text: chars.join(""), indices };
}

/**
 * Nudge a resolved range so the excerpt handed to the backend is valid markdown:
 * absorb emphasis markers hugging the match, and if that still leaves an
 * unmatched `**`, shed the leading one rather than emit a half-open bold run.
 */
function balanceEmphasis(source: string, rawStart: number, rawEnd: number): SourceRange | null {
  let start = rawStart;
  let end = rawEnd;
  while (start > 0 && source[start - 1] === "*") start -= 1;
  while (end < source.length && source[end] === "*") end += 1;

  const markers = source.slice(start, end).match(/\*\*/g)?.length ?? 0;
  if (markers % 2 === 1 && source.startsWith("**", start)) start += 2;

  return end > start ? { start, end } : null;
}

function locate(
  source: string,
  projection: MarkdownProjection,
  selected: string
): SourceRange | null {
  const needle = projectMarkdown(selected).text.trim();
  if (needle.length < 3) return null;

  let at = projection.text.indexOf(needle);
  if (at < 0) {
    at = projection.text.toLowerCase().indexOf(needle.toLowerCase());
  }
  let endAt = at >= 0 ? at + needle.length - 1 : -1;
  if (at < 0) {
    const span = locateByEndpoints(projection.text, needle);
    if (span) {
      at = span.start;
      endAt = span.end;
    }
  }
  if (at < 0 || endAt < at) return null;

  const start = projection.indices[at];
  const end = projection.indices[Math.min(endAt, projection.indices.length - 1)];
  if (start === undefined || end === undefined) return null;

  return balanceEmphasis(source, start, end + 1);
}

/** Browser table selections are cell\\tcell\\nrow — match first/last distinctive tokens. */
function locateByEndpoints(
  haystack: string,
  needle: string
): { start: number; end: number } | null {
  const tokens = needle
    .split(/\s+/)
    .map((t) => t.trim())
    .filter((t) => t.length >= 4);
  if (tokens.length < 2) return null;
  const first = tokens[0]!;
  const last = tokens[tokens.length - 1]!;
  const start = haystack.indexOf(first);
  if (start < 0) return null;
  const lastAt = haystack.indexOf(last, start);
  if (lastAt < start) return null;
  return { start, end: lastAt + last.length - 1 };
}

export interface MarkdownSourceMap {
  find(selected: string): SourceRange | null;
}

/** Projects `source` once and reuses it across lookups. */
export function createMarkdownSourceMap(source: string): MarkdownSourceMap {
  const projection = projectMarkdown(source);
  return {
    find: (selected: string) => locate(source, projection, selected),
  };
}

export function findSourceRange(source: string, selected: string): SourceRange | null {
  return createMarkdownSourceMap(source).find(selected);
}
