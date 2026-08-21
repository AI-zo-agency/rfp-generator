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
  return line.includes("|") && line.includes("-") && /^[ \t|:-–—]+$/.test(line);
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

function parseTableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isDashOnlyCell(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return true;
  return /^[-–—:*\s]+$/.test(t);
}

function isPlaceholderCell(cell: string): boolean {
  const t = (cell || "").trim();
  if (!t) return true;
  return /^(?:\.{2,}|…+|[-–—_*]+|n\/?a|tbd|null|none)$/i.test(t);
}

function isPipeNoiseLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  if (
    !/^[\s|.:…\-–—_*]+$/.test(trimmed) &&
    parseTableCells(trimmed).some((c) => !isPlaceholderCell(c))
  ) {
    return false;
  }
  const cells = parseTableCells(trimmed);
  return cells.length >= 1 && cells.every(isPlaceholderCell);
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (/^\|?[\s:\-–—]+\|[\s|:\-–—]+\|?$/.test(trimmed)) return true;
  const cells = parseTableCells(trimmed);
  return cells.length === 0 || cells.every(isDashOnlyCell);
}

function normalizeTableCellLabel(text: string): string {
  const t = (text || "").trim();
  if (!t) return t;
  const parts = t.split(/\s+/).filter(Boolean);
  if (parts.length >= 3 && parts.every((p) => p.length === 1)) {
    return parts.join("");
  }
  return t;
}

function trimEmptyEdgeCells(cells: string[]): string[] {
  let start = 0;
  let end = cells.length;
  while (start < end && !(cells[start] || "").trim()) start += 1;
  while (end > start && !(cells[end - 1] || "").trim()) end -= 1;
  return cells.slice(start, end);
}

function collapseSpacedLetterColumns(cells: string[]): string[] {
  const out: string[] = [];
  let run: string[] = [];
  const flush = () => {
    if (run.length === 0) return;
    if (run.length >= 3 && run.every((p) => p.length === 1 && /[A-Za-z]/i.test(p))) {
      out.push(run.join(""));
    } else {
      out.push(...run);
    }
    run = [];
  };
  for (const cell of cells) {
    const t = (cell || "").trim();
    if (t.length === 1 && /[A-Za-z]/i.test(t)) {
      run.push(t.toUpperCase());
    } else {
      flush();
      out.push(normalizeTableCellLabel(t));
    }
  }
  flush();
  return out;
}

function normalizeTableRows(headers: string[], rows: string[][]): string[][] {
  const colCount = Math.max(headers.length, 1);
  return rows.map((row) => {
    const cells = trimEmptyEdgeCells(row);
    if (cells.length === colCount) return cells;
    if (cells.length < colCount) {
      return [...cells, ...Array(colCount - cells.length).fill("")];
    }
    const head = cells.slice(0, colCount - 1);
    head.push(cells.slice(colCount - 1).join(" · "));
    return head;
  });
}

function repairProjectedTable(
  headers: string[],
  rows: string[][],
  options: { hadSeparator: boolean }
): { headers: string[]; rows: string[][] } | null {
  const { hadSeparator } = options;
  let h = collapseSpacedLetterColumns(headers.map((c) => (c || "").trim()));
  let r = rows
    .map((row) => trimEmptyEdgeCells(row.map((c) => (c || "").trim())))
    .filter(
      (row) =>
        !(row.length === 0 || row.every(isDashOnlyCell)) &&
        !row.every(isPlaceholderCell)
    );

  if (!hadSeparator) {
    const allRows = [h, ...r]
      .map((row) => collapseSpacedLetterColumns(row))
      .filter((row) => row.length > 0 && !row.every(isPlaceholderCell));
    if (allRows.length === 0) return null;
    const width = Math.max(...allRows.map((row) => row.length), 2);
    const first = allRows[0]!;
    const firstLooksLikeHeader =
      first.length >= 2 &&
      first.every((c) => c.length > 0 && c.length <= 28) &&
      /^(field|label|name|item|category|information|value|detail|description|role|rate)$/i.test(
        first[0] || ""
      );
    if (firstLooksLikeHeader && allRows.length > 1) {
      h = first;
      r = allRows.slice(1);
    } else {
      h = Array.from({ length: width }, () => "");
      r = allRows;
    }
  } else {
    const headersUseless =
      h.length === 0 ||
      h.every(isPlaceholderCell) ||
      (h.length >= 2 && h.filter((c) => isPlaceholderCell(c)).length / h.length >= 0.6);

    if (headersUseless && r.length > 0) {
      h = collapseSpacedLetterColumns(r[0]!.map((c) => (c || "").trim()));
      r = r.slice(1);
    }
  }
  if (h.length === 0 || (h.every(isPlaceholderCell) && r.length === 0) || h.length > 12) {
    return null;
  }
  // Allow empty synthetic headers for key-value cards (projection omits them too).
  if (h.every(isPlaceholderCell) && r.length > 0) {
    return { headers: h, rows: normalizeTableRows(h, r) };
  }
  if (h.every(isPlaceholderCell)) return null;
  return { headers: h, rows: normalizeTableRows(h, r) };
}

/**
 * Mirror MarkdownReportBody.expandInlineTableLines, keeping a map back to the
 * pre-expansion source so selections still resolve to real offsets.
 */
function expandInlineTablesWithMap(source: string): { text: string; map: number[] } {
  const normalized = source.replace(/\r\n/g, "\n");
  const normMap: number[] = [];
  {
    let oi = 0;
    for (let ni = 0; ni < normalized.length; ni += 1) {
      if (source[oi] === "\r" && source[oi + 1] === "\n") {
        normMap.push(oi + 1);
        oi += 2;
      } else {
        normMap.push(oi);
        oi += 1;
      }
    }
  }

  const out: string[] = [];
  const map: number[] = [];
  let lineStart = 0;
  while (lineStart <= normalized.length) {
    let lineEnd = normalized.indexOf("\n", lineStart);
    if (lineEnd === -1) lineEnd = normalized.length;
    const line = normalized.slice(lineStart, lineEnd);
    const trimmed = line.trim();
    const pipes = trimmed.match(/\|/g)?.length ?? 0;

    if (trimmed.includes("|") && pipes >= 6) {
      if (isPipeNoiseLine(trimmed)) {
        // Drop noise lines entirely from the expanded stream.
      } else {
      const lead = line.match(/^[ \t]*/)?.[0].length ?? 0;
      const trailMatch = line.match(/[ \t]*$/);
      const trail =
        trailMatch && trailMatch[0].length > 0 && trailMatch[0] !== line
          ? trailMatch[0].length
          : 0;
      const coreStart = lineStart + lead;
      const core = line.slice(lead, line.length - trail);

      let i = 0;
      while (i < core.length) {
        const abs = coreStart + i;
        const rest = core.slice(i);
        const m1 = rest.match(/^\|(\s*)\|(?=\s*[-:–—])/);
        if (m1) {
          out.push("|");
          map.push(normMap[abs] ?? abs);
          out.push("\n");
          map.push(normMap[abs] ?? abs);
          const second = abs + 1 + m1[1].length;
          out.push("|");
          map.push(normMap[second] ?? second);
          i += 1 + m1[1].length + 1;
          continue;
        }
        const m2 = rest.match(/^\|(\s+)(?=\|)/);
        if (m2) {
          out.push("|");
          map.push(normMap[abs] ?? abs);
          out.push("\n");
          map.push(normMap[abs] ?? abs);
          out.push("|");
          map.push(normMap[abs + 1 + m2[1].length] ?? abs);
          i += 1 + m2[1].length;
          continue;
        }
        out.push(core[i]!);
        map.push(normMap[abs] ?? abs);
        i += 1;
      }
      }
    } else if (!isPipeNoiseLine(trimmed)) {
      for (let i = 0; i < line.length; i += 1) {
        const abs = lineStart + i;
        out.push(line[i]!);
        map.push(normMap[abs] ?? abs);
      }
    }

    if (lineEnd < normalized.length) {
      out.push("\n");
      map.push(normMap[lineEnd] ?? lineEnd);
      lineStart = lineEnd + 1;
    } else {
      break;
    }
  }

  return { text: out.join(""), map };
}

function projectLineContent(
  line: string,
  lineStart: number,
  toSource: (i: number) => number,
  chars: string[],
  indices: number[],
  pushSpace: (at: number) => void,
  dropPendingSpace: () => void,
  keepPipes: boolean
): void {
  let i = 0;
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
      const tagStart = toSource(lineStart + i);
      const tagEnd = toSource(lineStart + i + tag.length - 1);
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
    if (char === " " || char === "\t" || (char === "|" && !keepPipes)) {
      pushSpace(toSource(lineStart + i));
      i += 1;
      continue;
    }
    chars.push(char);
    indices.push(toSource(lineStart + i));
    i += 1;
  }
}

function projectStructuredTable(
  headers: string[],
  rows: string[][],
  tableLines: string[],
  lineStarts: number[],
  toSource: (i: number) => number,
  chars: string[],
  indices: number[],
  pushSpace: (at: number) => void
): void {
  const normalizedHeaders = headers.map(normalizeTableCellLabel);
  const normalizedRows = normalizeTableRows(
    normalizedHeaders,
    rows.map((row) => row.map(normalizeTableCellLabel))
  );
  const haystacks = tableLines.map((l, idx) => ({
    line: l,
    start: lineStarts[idx] ?? 0,
  }));
  const locateCell = (cell: string): { start: number; end: number } => {
    if (!cell) {
      const at = toSource(lineStarts[0] ?? 0);
      return { start: at, end: at };
    }
    for (const { line, start } of haystacks) {
      const at = line.indexOf(cell);
      if (at >= 0) {
        const srcStart = toSource(start + at);
        const srcEnd = toSource(start + at + Math.max(cell.length - 1, 0));
        return { start: srcStart, end: srcEnd };
      }
    }
    const at = toSource(lineStarts[0] ?? 0);
    return { start: at, end: at };
  };

  const emitCell = (text: string) => {
    const located = locateCell(text);
    let i = 0;
    while (i < text.length) {
      GAP_TAG.lastIndex = i;
      const gap = GAP_TAG.exec(text);
      if (gap && gap.index === i) {
        const tag = gap[0];
        const visible = visibleGapProjection(tag);
        const tagStart = located.start;
        const tagEnd = located.end;
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
      const ch = text[i]!;
      // Prefer per-character source when the cell text is a contiguous source span.
      const srcAt =
        text.length > 1 && located.end > located.start
          ? located.start
          : located.start;
      if (ch === " " || ch === "\t") {
        pushSpace(srcAt);
      } else {
        chars.push(ch);
        indices.push(srcAt);
      }
      i += 1;
    }
  };

  for (const cell of normalizedHeaders) {
    if (!cell || isPlaceholderCell(cell)) continue;
    pushSpace(locateCell(cell).start);
    emitCell(cell);
  }
  for (const row of normalizedRows) {
    for (const cell of row) {
      if (!cell) continue;
      pushSpace(locateCell(cell).start);
      emitCell(cell);
    }
  }
}

export function projectMarkdown(source: string): MarkdownProjection {
  const { text: expanded, map } = expandInlineTablesWithMap(source);
  const toSource = (i: number) =>
    map.length === 0 ? i : (map[Math.min(Math.max(i, 0), map.length - 1)] ?? i);

  const chars: string[] = [];
  const indices: number[] = [];

  const pushSpace = (at: number) => {
    if (chars.length === 0) return;
    if (chars[chars.length - 1] === " ") return;
    chars.push(" ");
    indices.push(at);
  };

  const dropPendingSpace = () => {
    if (chars[chars.length - 1] === " ") {
      chars.pop();
      indices.pop();
    }
  };

  const lines = expanded.split("\n");
  const lineStarts: number[] = [];
  {
    let at = 0;
    for (const line of lines) {
      lineStarts.push(at);
      at += line.length + 1;
    }
  }

  let lineIndex = 0;
  while (lineIndex < lines.length) {
    const line = lines[lineIndex] ?? "";
    const trimmed = line.trim();
    const lineStart = lineStarts[lineIndex] ?? 0;
    const lineEnd = lineStart + line.length;

    if (!trimmed) {
      pushSpace(toSource(lineEnd));
      lineIndex += 1;
      continue;
    }

    if (isPipeNoiseLine(trimmed)) {
      pushSpace(toSource(lineEnd));
      lineIndex += 1;
      continue;
    }

    const dropped =
      THEMATIC_BREAK.test(line) ||
      CODE_FENCE.test(line) ||
      REFERENCES_ONLY.test(line);

    if (dropped) {
      pushSpace(toSource(lineEnd));
      lineIndex += 1;
      continue;
    }

    if (trimmed.includes("|") && parseTableCells(trimmed).length >= 2) {
      const tableLines: string[] = [];
      const tableLineStarts: number[] = [];
      while (lineIndex < lines.length) {
        const current = lines[lineIndex]?.trim() ?? "";
        if (!current) {
          let peek = lineIndex + 1;
          while (peek < lines.length && !(lines[peek]?.trim() ?? "")) peek += 1;
          if (peek < lines.length && lines[peek]!.trim().includes("|")) {
            lineIndex = peek;
            continue;
          }
          break;
        }
        if (isPipeNoiseLine(current)) {
          lineIndex += 1;
          continue;
        }
        if (!current.includes("|") || parseTableCells(current).length < 2) break;
        tableLines.push(current);
        tableLineStarts.push(lineStarts[lineIndex] ?? 0);
        lineIndex += 1;
      }

      const dataLines = tableLines.filter((row) => !isTableSeparatorLine(row));
      const parsed = dataLines
        .map(parseTableCells)
        .filter((cells) => !(cells.length === 0 || cells.every(isDashOnlyCell)));

      if (parsed.length > 0) {
        const hadSeparator = tableLines.some((row) => isTableSeparatorLine(row));
        const repaired = repairProjectedTable(parsed[0]!, parsed.slice(1), {
          hadSeparator,
        });
        if (repaired) {
          projectStructuredTable(
            repaired.headers,
            repaired.rows,
            tableLines,
            tableLineStarts,
            toSource,
            chars,
            indices,
            pushSpace
          );
        } else {
          for (let t = 0; t < tableLines.length; t += 1) {
            const rawLine = tableLines[t]!;
            if (isPipeNoiseLine(rawLine) || isTableSeparatorLine(rawLine)) continue;
            const cells = parseTableCells(rawLine).filter((c) => !isPlaceholderCell(c));
            const joined = cells.join(" · ");
            if (!joined) continue;
            const rawStart = tableLineStarts[t]!;
            for (let k = 0; k < joined.length; k += 1) {
              const ch = joined[k]!;
              if (ch === " " || ch === "\t") {
                pushSpace(toSource(rawStart));
              } else {
                chars.push(ch);
                indices.push(toSource(rawStart));
              }
            }
            pushSpace(toSource(rawStart + rawLine.length));
          }
        }
      }
      continue;
    }

    if (isTableDelimiter(line)) {
      pushSpace(toSource(lineEnd));
      lineIndex += 1;
      continue;
    }

    projectLineContent(
      line,
      lineStart,
      toSource,
      chars,
      indices,
      pushSpace,
      dropPendingSpace,
      false
    );
    pushSpace(toSource(lineEnd));
    lineIndex += 1;
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
