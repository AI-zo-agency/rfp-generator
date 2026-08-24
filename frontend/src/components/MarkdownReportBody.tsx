import { humanizeGapTag, isInternalScanTag, isManualFillTag } from "@/lib/gap-tag-humanize";

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; text: string }
  | { type: "designer_note"; text: string }
  | { type: "gap_callout"; tag: string }
  | { type: "hr" };

function isThematicBreak(line: string): boolean {
  return /^(-{3,}|\*{3,}|_{3,})$/.test(line.trim());
}

function parseSubheadingLine(line: string): string | null {
  const trimmed = line.trim();
  const boldOnly = trimmed.match(/^\*\*([^*]+)\*\*:?\s*$/);
  if (boldOnly) return boldOnly[1].trim();
  const colonLead = trimmed.match(/^([A-Z0-9][^.\n]{2,88}):\s*$/);
  if (colonLead && !colonLead[1].includes("|")) return colonLead[1].trim();
  return null;
}

function tryManualFillCallout(text: string): string | null {
  const trimmed = (text || "").trim();
  if (!isManualFillTag(trimmed)) return null;
  return trimmed.endsWith("]") ? trimmed : `${trimmed}]`;
}

function InlineGapTag({
  tag,
  highlighted = false,
  compact = false,
}: {
  tag: string;
  highlighted?: boolean;
  compact?: boolean;
}) {
  const h = humanizeGapTag(tag);
  if (compact) {
    return (
      <span
        className={`inline text-[0.85em] italic ${
          highlighted ? "text-amber-800" : "text-zo-text-muted"
        }`}
        title={h.action || h.detail || h.title}
      >
        TBD — {h.title}
      </span>
    );
  }
  return (
    <span
      className={`inline rounded-md border px-1.5 py-0.5 text-[0.85em] leading-relaxed ${
        highlighted
          ? "proposal-flag-inline-highlight border-amber-400/60 bg-amber-100/80 text-orange-950"
          : "border-violet-300/40 bg-violet-50 text-violet-950"
      }`}
      role="note"
      title={h.action}
    >
      <strong>{h.title}</strong>
      {h.detail ? <> — {h.detail}</> : null}
    </span>
  );
}

function tagMatchesHighlight(tag: string, highlights: string[]): boolean {
  const normalized = tag.trim();
  return highlights.some((h) => {
    const needle = h.trim();
    if (!needle) return false;
    return normalized === needle || normalized.includes(needle) || needle.includes(normalized);
  });
}

function GapCallout({ tag, highlighted = false }: { tag: string; highlighted?: boolean }) {
  const h = humanizeGapTag(tag);
  return (
    <div
      className={`my-5 max-w-[68ch] rounded-xl border px-4 py-3.5 ${
        highlighted
          ? "border-zo-orange/45 bg-amber-50/90 shadow-[0_0_0_3px_rgba(239,80,24,0.12)] proposal-flag-inline-highlight"
          : "border-violet-300/30 bg-gradient-to-b from-violet-50/95 to-slate-50/95"
      }`}
      role="note"
      aria-label={h.title}
      ref={
        highlighted
          ? (node) => node?.scrollIntoView({ behavior: "smooth", block: "center" })
          : undefined
      }
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="inline-flex rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-violet-800">
          Action needed
        </span>
        {h.owner ? (
          <span className="text-xs font-bold text-violet-700">{h.owner}</span>
        ) : null}
      </div>
      <p className={`mb-1 text-[15px] font-bold leading-snug ${highlighted ? "text-orange-900" : "text-indigo-950"}`}>
        {h.title}
      </p>
      <p className="mb-1.5 text-sm leading-relaxed text-violet-900/90">{h.detail}</p>
      <p className="m-0 text-xs leading-relaxed text-violet-800/85">{h.action}</p>
    </div>
  );
}

function pushParagraphBlock(blocks: Block[], paragraphLines: string[]) {
  if (paragraphLines.length === 0) return;

  if (paragraphLines.length === 1) {
    const sub = parseSubheadingLine(paragraphLines[0]!);
    if (sub) {
      blocks.push({ type: "heading", level: 3, text: sub });
      return;
    }
    const designer = tryDesignerNoteFromParagraph(paragraphLines[0]!);
    if (designer) {
      blocks.push({ type: "designer_note", text: designer });
      return;
    }
    const gapTag = tryManualFillCallout(paragraphLines[0]!);
    if (gapTag) {
      if (isInternalScanTag(gapTag)) {
        return;
      }
      blocks.push({ type: "gap_callout", tag: gapTag });
      return;
    }
  }

  if (paragraphLines.length >= 2) {
    const sub = parseSubheadingLine(paragraphLines[0]!);
    if (sub) {
      blocks.push({ type: "heading", level: 3, text: sub });
      const rest = paragraphLines.slice(1).join(" ").trim();
      if (rest) blocks.push({ type: "paragraph", text: rest });
      return;
    }
  }

  blocks.push({ type: "paragraph", text: paragraphLines.join(" ") });
}

/** Strip LLM leaks (retrieval: / ```markdown fences) and OCR HTML comments. */
export function stripManuscriptDisplayArtifacts(text: string): string {
  let t = (text || "").trim();
  t = t.replace(/^(?:retrieval|context|kb|source|markdown)\s*:\s*\n?/i, "");
  const wrapped = t.match(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$/i);
  if (wrapped) {
    t = wrapped[1].trim();
  } else {
    t = t.replace(/^```(?:markdown|md)?\s*\n/i, "");
    t = t.replace(/\n```\s*$/i, "");
  }
  t = t.replace(/<!--[\s\S]*?-->/g, "");
  return t.trim();
}

/** Strip internal KB evidence markers ([E1], [E12, E13], …) from client-facing copy. */
export function stripEvidenceCitations(text: string): string {
  let t = stripManuscriptDisplayArtifacts(text);
  // Comma lists: [E12, E13, E14]
  t = t.replace(/\s*\*{0,2}\[\s*E\d+(?:\s*[,;]\s*E\d+)+\s*\]\*{0,2}/gi, "");
  // Singles: [E1], **[E14]**
  t = t.replace(/\s*\*{0,2}\[E\d+\]\*{0,2}/gi, "");
  // Orphaned References lines that only listed evidence ids
  t = t.replace(
    /^\s*\**\s*References?\s*\**\s*:?\s*\**\s*(?:\[?\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]?)?\s*$/gim,
    ""
  );
  t = t.replace(/\[PRICING FLAG:[^\]]*\]/gi, "");
  t = t.replace(
    /\s*\[MANUAL\s+FILL:[^\]]*(?:deterministic\.fabricated_fact|deterministic\.unverified|deferred information|upon_request)[^\]]*\]/gi,
    ""
  );
  t = t.replace(/[ \t]{2,}/g, " ");
  t = t.replace(/\n{3,}/g, "\n\n");
  t = t
    .split("\n")
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed.includes("|")) return true;
      const inner = trimmed.replace(/^\|/, "").replace(/\|$/, "");
      const cells = inner.split("|").map((c) => c.trim());
      if (cells.length >= 1 && cells.every((c) => !c)) return false;
      // Ellipsis / placeholder-only pipe rows
      if (
        cells.length >= 1 &&
        cells.every((c) => !c || /^(?:\.{2,}|…+|[-–—_*]+)$/i.test(c))
      ) {
        return false;
      }
      return true;
    })
    .join("\n");
  return t.trim();
}

function isDashOnlyCell(cell: string): boolean {
  const t = (cell || "").trim();
  if (!t) return true;
  return /^[-–—:*\s]+$/.test(t);
}

function isEmptyOrDashRow(cells: string[]): boolean {
  return cells.length === 0 || cells.every(isDashOnlyCell);
}

/** Placeholder / ellipsis cells LLMs emit instead of real headers. */
function isPlaceholderCell(cell: string): boolean {
  const t = (cell || "").trim();
  if (!t) return true;
  return /^(?:\.{2,}|…+|[-–—_*]+|n\/?a|tbd|null|none)$/i.test(t);
}

/**
 * Pipe noise: `| | | | |`, `||||`, or ellipsis-only rows. These must never
 * render as paragraphs (raw pipes) or as table chrome.
 */
function isPipeNoiseLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  if (!/^[\s|.:…\-–—_*]+$/.test(trimmed) && parseTableRow(trimmed).some((c) => !isPlaceholderCell(c))) {
    return false;
  }
  const cells = parseTableRow(trimmed);
  return cells.length >= 1 && cells.every(isPlaceholderCell);
}

function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  if (isPipeNoiseLine(trimmed)) return false;
  const cells = parseTableRow(trimmed);
  if (cells.length < 2) return false;
  if (cells.every((c) => !(c || "").trim())) return false;
  return true;
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  if (/^\|?[\s:\-–—]+\|[\s|:\-–—]+\|?$/.test(trimmed)) return true;
  return isEmptyOrDashRow(parseTableRow(trimmed));
}

function parseTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Drop empty leading/trailing cells from expand artifacts (`|| Foundation`). */
function trimEmptyEdgeCells(cells: string[]): string[] {
  let start = 0;
  let end = cells.length;
  while (start < end && !(cells[start] || "").trim()) start += 1;
  while (end > start && !(cells[end - 1] || "").trim()) end -= 1;
  return cells.slice(start, end);
}

function normalizeTableRows(headers: string[], rows: string[][]): string[][] {
  const colCount = Math.max(headers.length, 1);
  // Fee Detail "stair-step" rows leave Phase blank (`| | Deliverable | $X |`).
  // Never strip that leading blank or the deliverable shifts into the Phase column.
  const preserveLeadingBlank = /phase/i.test((headers[0] || "").replace(/\*\*/g, "").trim());
  return rows.map((row) => {
    const cells = preserveLeadingBlank
      ? row.map((c) => (c || "").trim())
      : trimEmptyEdgeCells(row);
    if (cells.length === colCount) {
      return cells;
    }
    if (cells.length < colCount) {
      if (preserveLeadingBlank) {
        return [...Array(colCount - cells.length).fill(""), ...cells];
      }
      return [...cells, ...Array(colCount - cells.length).fill("")];
    }
    const head = cells.slice(0, colCount - 1);
    head.push(cells.slice(colCount - 1).join(" · "));
    return head;
  });
}

/** "P H A S E" spacing from bad LLM exports → "PHASE". */
function normalizeTableCellLabel(text: string): string {
  const t = (text || "").trim();
  if (!t) return t;
  const parts = t.split(/\s+/).filter(Boolean);
  if (parts.length >= 3 && parts.every((p) => p.length === 1)) {
    return parts.join("");
  }
  return t;
}

/** Merge consecutive single-letter header columns: P|H|A|S|E → PHASE. */
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

function dropEmptyColumns(
  headers: string[],
  rows: string[][]
): { headers: string[]; rows: string[][] } {
  if (headers.length === 0) return { headers, rows };
  const keep: number[] = [];
  for (let c = 0; c < headers.length; c += 1) {
    const headerEmpty = isPlaceholderCell(headers[c] || "");
    const allRowsEmpty = rows.every((row) => isPlaceholderCell(row[c] || ""));
    if (!(headerEmpty && allRowsEmpty)) keep.push(c);
  }
  if (keep.length === headers.length || keep.length === 0) {
    return { headers, rows };
  }
  return {
    headers: keep.map((i) => headers[i]!),
    rows: rows.map((row) => keep.map((i) => row[i] || "")),
  };
}

function repairTableBlock(
  headers: string[],
  rows: string[][],
  options: { hadSeparator: boolean }
): { headers: string[]; rows: string[][] } | null {
  const { hadSeparator } = options;
  let h = collapseSpacedLetterColumns(headers.map((c) => (c || "").trim()));
  let r = rows
    .map((row) => trimEmptyEdgeCells(row.map((c) => (c || "").trim())))
    .filter((row) => !isEmptyOrDashRow(row) && !row.every(isPlaceholderCell));

  // No markdown separator → every pipe row is data (common in bios / key-value cards).
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

  if (h.length > 12) {
    return null;
  }

  // Empty synthetic headers are OK for key-value cards (thead hidden).
  if (h.length === 0) {
    return null;
  }
  if (h.every(isPlaceholderCell) && r.length === 0) {
    return null;
  }

  const normalized = normalizeTableRows(h, r);
  // Keep empty header slots so 2-col key/value layout survives dropEmptyColumns.
  const trimmed =
    h.every(isPlaceholderCell) && r.length > 0
      ? { headers: h, rows: normalized }
      : dropEmptyColumns(h, normalized);
  if (trimmed.headers.length === 0) return null;
  if (trimmed.rows.length === 0 && trimmed.headers.every(isPlaceholderCell)) return null;
  return trimmed;
}

/** Last resort: force pipe lines into a real HTML table — never a dotted list. */
function tableFromRawLines(
  rawLines: string[]
): { headers: string[]; rows: string[][] } | null {
  const rows = rawLines
    .filter((l) => !isPipeNoiseLine(l) && !isTableSeparator(l))
    .map((l) => trimEmptyEdgeCells(parseTableRow(l)))
    .filter((row) => row.length > 0 && !row.every(isPlaceholderCell));
  if (rows.length === 0) return null;
  const width = Math.max(...rows.map((row) => row.length), 2);
  const headers = Array.from({ length: width }, () => "");
  return { headers, rows: normalizeTableRows(headers, rows) };
}

function sanitizeTableBlock(
  headers: string[],
  rows: string[][],
  rawLines: string[]
): { headers: string[]; rows: string[][] } | null {
  const hadSeparator = rawLines.some((line) => isTableSeparator(line));
  const repaired = repairTableBlock(headers, rows, { hadSeparator });
  const base = repaired ?? tableFromRawLines(rawLines);
  if (!base) return null;
  return rollupFeeDetailTableForDisplay(base.headers, base.rows);
}

function _stripMdBold(text: string): string {
  return text.replace(/\*\*/g, "").trim();
}

function _parseFeeCell(cell: string): number | null {
  const cleaned = _stripMdBold(cell).replace(/,/g, "");
  const match = cleaned.match(/\$?\s*(-?\d+(?:\.\d+)?)/);
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) ? n : null;
}

function _formatFeeUsd(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Number.isInteger(amount) ? 0 : 2,
  }).format(amount);
}

function _isFeeDetailHeader(headers: string[]): boolean {
  const labels = headers.map((h) => _stripMdBold(h).toLowerCase());
  const hasPhase = labels.some((h) => h.includes("phase"));
  const hasFee = labels.some((h) => /^(fee|amount|cost|price)\b/.test(h) || h.includes("fee"));
  return hasPhase && hasFee;
}

function _isTotalPhaseLabel(label: string): boolean {
  const t = _stripMdBold(label).toLowerCase();
  return t === "total" || t.startsWith("total ") || t.includes("professional fees total");
}

/**
 * Client display: collapse stair-step Fee Detail tables (blank Phase cells
 * continuing the prior phase) into one Phase | Scope | Fee row per phase.
 * Does not change stored markdown — view-only polish for Review / manuscript.
 */
function rollupFeeDetailTableForDisplay(
  headers: string[],
  rows: string[][]
): { headers: string[]; rows: string[][] } {
  if (!_isFeeDetailHeader(headers) || rows.length === 0) {
    return { headers, rows };
  }

  const phaseIdx = headers.findIndex((h) => /phase/i.test(_stripMdBold(h)));
  const feeIdx = headers.findIndex((h) => {
    const t = _stripMdBold(h).toLowerCase();
    return /^(fee|amount|cost|price)\b/.test(t) || t.includes("fee");
  });
  if (phaseIdx < 0 || feeIdx < 0) return { headers, rows };

  const hadBlankPhase = rows.some((row, i) => {
    const phase = (row[phaseIdx] || "").trim();
    if (_isTotalPhaseLabel(phase)) return false;
    return i > 0 && !phase;
  });
  const uniquePhases = new Set(
    rows
      .map((row) => _stripMdBold(row[phaseIdx] || ""))
      .filter((p) => p && !_isTotalPhaseLabel(p))
  );
  // Roll up when blank phases exist, or many deliverable rows for few phases.
  if (!hadBlankPhase && rows.length <= uniquePhases.size + 1) {
    // Still normalize header labels for consistency.
    const normalizedHeaders = headers.map((h, i) => {
      if (i === phaseIdx) return "Phase";
      if (i === feeIdx) return "Fee";
      if (/deliverable|service|description|scope/i.test(_stripMdBold(h))) return "Scope";
      return h;
    });
    return { headers: normalizedHeaders, rows };
  }

  type Bucket = { phase: string; scopes: string[]; amount: number; hasAmount: boolean };
  const order: string[] = [];
  const buckets = new Map<string, Bucket>();
  let currentPhase = "";

  for (const row of rows) {
    const phaseRaw = _stripMdBold(row[phaseIdx] || "");
    if (_isTotalPhaseLabel(phaseRaw)) continue;
    if (phaseRaw) currentPhase = phaseRaw;
    if (!currentPhase) continue;

    let bucket = buckets.get(currentPhase);
    if (!bucket) {
      bucket = { phase: currentPhase, scopes: [], amount: 0, hasAmount: false };
      buckets.set(currentPhase, bucket);
      order.push(currentPhase);
    }

    for (let i = 0; i < row.length; i += 1) {
      if (i === phaseIdx || i === feeIdx) continue;
      const cell = _stripMdBold(row[i] || "");
      if (cell && !bucket.scopes.includes(cell)) bucket.scopes.push(cell);
    }
    const fee = _parseFeeCell(row[feeIdx] || "");
    if (fee != null) {
      bucket.amount += fee;
      bucket.hasAmount = true;
    }
  }

  if (order.length === 0) return { headers, rows };

  const rolled: string[][] = order.map((phase) => {
    const b = buckets.get(phase)!;
    let scope =
      b.scopes.length > 4
        ? `${b.scopes.slice(0, 4).join("; ")}; +${b.scopes.length - 4} more`
        : b.scopes.join("; ");
    if (scope.length > 200) scope = `${scope.slice(0, 197).replace(/;?\s*$/, "")}…`;
    if (!scope) scope = "Professional services";
    return [phase, scope, b.hasAmount ? _formatFeeUsd(b.amount) : "—"];
  });

  const total = order.reduce((sum, phase) => {
    const b = buckets.get(phase);
    return sum + (b?.hasAmount ? b.amount : 0);
  }, 0);
  if (total > 0) {
    rolled.push(["**Total**", "", `**${_formatFeeUsd(total)}**`]);
  }

  return { headers: ["Phase", "Scope", "Fee"], rows: rolled };
}

function ProposalTable({
  headers,
  rows,
  compact,
  highlights,
  blockIndex,
}: {
  headers: string[];
  rows: string[][];
  compact: boolean;
  highlights: string[];
  blockIndex: number;
}) {
  const cellPad = compact ? "px-3 py-2.5" : "px-4 py-3";
  const headPad = compact ? "px-3 py-2" : "px-4 py-2.5";
  const textSize = compact ? "text-sm" : "text-[13px]";
  const showHeader = headers.some((h) => !isPlaceholderCell(h));
  const feeColIndexes = new Set(
    headers
      .map((h, i) => (/fee|amount|cost|price|\$/i.test(_stripMdBold(h)) ? i : -1))
      .filter((i) => i >= 0)
  );
  return (
    <div className="my-4 overflow-x-auto rounded-xl border border-zo-border bg-white shadow-sm">
      <table
        className={`w-full min-w-[min(100%,480px)] border-collapse text-left ${textSize}`}
      >
        {showHeader ? (
          <thead>
            <tr className="border-b-2 border-zo-border bg-[var(--zo-surface)] text-xs uppercase tracking-wide text-zo-text-muted">
              {headers.map((header, headerIndex) => (
                <th
                  key={`${blockIndex}-h-${headerIndex}`}
                  className={`min-w-[5.5rem] max-w-[28rem] border-r border-zo-border/50 align-top whitespace-normal last:border-r-0 ${headPad} font-bold ${
                    feeColIndexes.has(headerIndex) ? "text-right" : ""
                  }`}
                >
                  {renderInline(header, highlights)}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, rowIndex) => {
            const isTotal = _isTotalPhaseLabel(row[0] || "");
            return (
              <tr
                key={rowIndex}
                className={`border-b border-zo-border/70 align-top last:border-0 ${
                  isTotal ? "bg-[var(--zo-surface)] font-semibold" : ""
                }`}
              >
                {headers.map((_, cellIndex) => (
                  <td
                    key={cellIndex}
                    className={`min-w-[5.5rem] max-w-[28rem] border-r border-zo-border/40 align-top whitespace-normal last:border-r-0 ${cellPad} ${
                      compact && !isTotal ? "text-zo-text-secondary" : ""
                    } ${
                      cellIndex === 0 && !showHeader ? "font-semibold text-foreground" : ""
                    } ${feeColIndexes.has(cellIndex) ? "text-right tabular-nums" : ""} ${
                      cellIndex === 0 && showHeader ? "font-medium text-foreground" : ""
                    }`}
                  >
                    {renderInline(row[cellIndex] || "", highlights, {
                      compactHandoff: true,
                    })}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function expandInlineTableLines(text: string): string {
  return text
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed.includes("|")) return line;
      if (isPipeNoiseLine(trimmed)) return "";
      const pipes = trimmed.match(/\|/g)?.length ?? 0;
      // LLM often emits header + separator + rows on one line — split row boundaries.
      if (pipes < 6) return line;
      let expanded = trimmed.replace(/\|\s*\|(?=\s*[-:–—])/g, "|\n|");
      expanded = expanded.replace(/\|\s+(?=\|)/g, "|\n|");
      // Collapse expand artifacts: `|| Foundation` → `| Foundation`
      expanded = expanded
        .split("\n")
        .map((row) => row.replace(/^\|{2,}/, "|").replace(/\|{2,}$/, "|"))
        .join("\n");
      return expanded;
    })
    .join("\n");
}

function parseBlocks(body: string): Block[] {
  const normalized = expandInlineTableLines(body.replace(/\r\n/g, "\n"));
  const lines = normalized.split("\n");
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    // Never leak `| | | |` / ellipsis pipe rows as paragraphs.
    if (isPipeNoiseLine(trimmed)) {
      index += 1;
      continue;
    }

    if (isThematicBreak(trimmed)) {
      blocks.push({ type: "hr" });
      index += 1;
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      blocks.push({
        type: "heading",
        level: headingMatch[1].length,
        text: headingMatch[2].trim(),
      });
      index += 1;
      continue;
    }

    if (isTableRow(trimmed)) {
      const tableLines: string[] = [];
      while (index < lines.length) {
        const current = lines[index]?.trim() ?? "";
        if (!current) {
          // Blank lines inside markdown tables (common after LLM edits) —
          // keep scanning if the next non-empty line is still a pipe row.
          let peek = index + 1;
          while (peek < lines.length && !(lines[peek]?.trim() ?? "")) peek += 1;
          if (peek < lines.length && isTableRow(lines[peek]!.trim())) {
            index = peek;
            continue;
          }
          break;
        }
        if (!isTableRow(current)) break;
        tableLines.push(current);
        index += 1;
      }
      const dataLines = tableLines.filter((row) => !isTableSeparator(row));
      const parsed = dataLines
        .map(parseTableRow)
        .filter((cells) => !isEmptyOrDashRow(cells));
      if (parsed.length > 0) {
        const headers = parsed[0]!;
        const rows = parsed.slice(1);
        const sanitized = sanitizeTableBlock(headers, rows, tableLines);
        if (sanitized && sanitized.headers.length > 0) {
          blocks.push({
            type: "table",
            headers: sanitized.headers,
            rows: sanitized.rows,
          });
        }
      }
      continue;
    }

    if (/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      const ordered = /^\d+\.\s+/.test(trimmed);
      const items: string[] = [];
      while (index < lines.length) {
        const current = lines[index]?.trim() ?? "";
        if (!current) {
          let peek = index + 1;
          while (peek < lines.length && !(lines[peek]?.trim() ?? "")) peek += 1;
          const next = peek < lines.length ? (lines[peek]?.trim() ?? "") : "";
          if (
            next &&
            (ordered ? /^\d+\.\s+/.test(next) : /^[-*]\s+/.test(next))
          ) {
            index = peek;
            continue;
          }
          break;
        }
        if (ordered) {
          const match = current.match(/^\d+\.\s+(.+)$/);
          if (!match) break;
          items.push(match[1]);
        } else if (/^[-*]\s+/.test(current)) {
          items.push(current.replace(/^[-*]\s+/, ""));
        } else {
          break;
        }
        index += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const current = lines[index] ?? "";
      const currentTrimmed = current.trim();
      if (!currentTrimmed) break;
      if (
        /^(#{1,4})\s+/.test(currentTrimmed) ||
        isTableRow(currentTrimmed) ||
        /^[-*]\s+/.test(currentTrimmed) ||
        /^\d+\.\s+/.test(currentTrimmed)
      ) {
        break;
      }
      paragraphLines.push(currentTrimmed);
      index += 1;
    }
    pushParagraphBlock(blocks, paragraphLines);
  }

  return blocks;
}

function tryDesignerNoteFromParagraph(text: string): string | null {
  const trimmed = text.trim();
  const bracket = trimmed.match(
    /^\[(?:DESIGNER NOTE|Designer Note)\s*:?\s*([\s\S]*)\]\s*$/i
  );
  if (bracket) return bracket[1].trim();
  // LLMs often emit bold/plain labels instead of the canonical bracket tag.
  const prose = trimmed.match(
    /^(?:\*\*|__)?\s*Designer\s+Note(?:\*\*|__)?\s*:\s*([\s\S]+)$/i
  );
  if (prose) {
    return prose[1].replace(/^(?:\*\*|__)+|(?:\*\*|__)+$/g, "").trim();
  }
  return null;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildInlinePattern(highlightTexts: string[]): RegExp {
  const tagPattern = String.raw`\*\*[^*]+\*\*|\[(?:VERIFY|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER|MANUAL FILL)[^\]]*\]`;
  const unique = [...new Set(highlightTexts.map((h) => h.trim()).filter(Boolean))].sort(
    (a, b) => b.length - a.length
  );
  if (unique.length === 0) {
    return new RegExp(`(${tagPattern})`, "gi");
  }
  const highlights = unique.map(escapeRegex).join("|");
  return new RegExp(`(${highlights}|${tagPattern})`, "gi");
}

function renderInline(
  text: string | undefined | null,
  highlightTexts: string[] = [],
  options: { compactHandoff?: boolean } = {}
) {
  let markAssigned = false;
  const safe = text ?? "";
  const highlights = highlightTexts.filter(
    (h) => h?.trim() && !isInternalScanTag(h) && !/deterministic\./i.test(h)
  );
  const parts = safe.split(buildInlinePattern(highlights));
  const normalizedHighlights = new Set(
    highlights.map((h) => h.trim()).filter(Boolean)
  );

  return parts.map((part, index) => {
    if (!part) return null;

    if (/^\[MANUAL\s+FILL/i.test(part)) {
      if (isInternalScanTag(part)) return null;
      const highlighted = tagMatchesHighlight(part, highlights);
      return (
        <InlineGapTag
          key={index}
          tag={part}
          highlighted={highlighted}
          compact={options.compactHandoff === true}
        />
      );
    }

    if (normalizedHighlights.has(part.trim()) || normalizedHighlights.has(part)) {
      const assignRef = !markAssigned;
      markAssigned = true;
      return (
        <mark
          key={index}
          ref={assignRef ? (node) => node?.scrollIntoView({ behavior: "smooth", block: "center" }) : undefined}
          className="proposal-flag-inline-highlight"
          title="Flagged for submission review"
        >
          {part}
        </mark>
      );
    }

    if (part.startsWith("**") && part.endsWith("**")) {
      const inner = part.slice(2, -2);
      // LLM sometimes wraps the entire section in **…** — don't render that as bold.
      if (inner.split(/\s+/).filter(Boolean).length > 12) {
        return <span key={index}>{inner}</span>;
      }
      return (
        <strong key={index} className="font-semibold text-foreground">
          {inner}
        </strong>
      );
    }
    if (/^\[VERIFY/i.test(part)) {
      const h = humanizeGapTag(part);
      const highlighted = tagMatchesHighlight(part, highlightTexts);
      return (
        <span
          key={index}
          className={`inline rounded-md border px-1.5 py-0.5 text-[0.85em] leading-relaxed ${
            highlighted
              ? "proposal-flag-inline-highlight border-amber-400/60 bg-amber-100/80 text-orange-950"
              : "border-red-300/50 bg-red-50 text-red-950"
          }`}
          title={h.action}
        >
          <strong>{h.title}</strong>
          {h.detail ? <> — {h.detail}</> : null}
        </span>
      );
    }
    if (/^\[PLACEHOLDER/i.test(part) || /^\[INSERT/i.test(part) || /^\[TBD/i.test(part)) {
      return (
        <span
          key={index}
          className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900"
          title="Fill in before submit"
        >
          {part}
        </span>
      );
    }
    if (/^\[FLAG/i.test(part) || /^\[DESIGNER NOTE/i.test(part)) {
      return (
        <span
          key={index}
          className="rounded bg-zo-teal/15 px-1.5 py-0.5 text-xs font-semibold text-zo-teal"
        >
          {part}
        </span>
      );
    }
    return <span key={index}>{part}</span>;
  });
}

export function MarkdownReportBody({
  body,
  variant = "report",
  highlightTexts = [],
}: {
  body: string;
  variant?: "report" | "document" | "chat";
  highlightTexts?: string[];
}) {
  const blocks = parseBlocks(
    variant === "document" ? stripEvidenceCitations(body) : body
  );
  const highlights = highlightTexts.filter((h) => h?.trim());

  if (variant === "document") {
    return (
      <div className="proposal-prose proposal-prose--manuscript">
        {blocks.map((block, index) => {
          if (block.type === "hr") {
            return (
              <hr
                key={index}
                className="proposal-manuscript-divider"
                aria-hidden
              />
            );
          }

          if (block.type === "heading") {
            if (block.level === 1) return <h1 key={index}>{renderInline(block.text, highlights)}</h1>;
            if (block.level === 2) return <h2 key={index}>{renderInline(block.text, highlights)}</h2>;
            if (block.level === 3) return <h3 key={index}>{renderInline(block.text, highlights)}</h3>;
            return <h4 key={index}>{renderInline(block.text, highlights)}</h4>;
          }

          if (block.type === "table") {
            return (
              <ProposalTable
                key={index}
                blockIndex={index}
                headers={block.headers}
                rows={block.rows}
                compact={false}
                highlights={highlights}
              />
            );
          }

          if (block.type === "list") {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <ListTag key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={`${index}-li-${itemIndex}`}>
                  {renderInline(item ?? "", highlights)}
                </li>
              ))}
              </ListTag>
            );
          }

          if (block.type === "designer_note") {
            return (
              <div
                key={index}
                className="proposal-designer-note-callout"
                role="note"
              >
                <p className="proposal-designer-note-label">Designer note</p>
                <p className="proposal-designer-note-body">
                  {renderInline(block.text, highlights)}
                </p>
              </div>
            );
          }

          if (block.type === "gap_callout") {
            if (isInternalScanTag(block.tag)) return null;
            return (
              <GapCallout
                key={index}
                tag={block.tag}
                highlighted={tagMatchesHighlight(block.tag, highlights)}
              />
            );
          }

          if (block.type === "paragraph") {
            return <p key={index}>{renderInline(block.text, highlights)}</p>;
          }

          return null;
        })}
      </div>
    );
  }

  const compact = variant === "chat";
  const stackClass = compact
    ? "proposal-section-chat-md space-y-2 text-[14px] leading-relaxed text-inherit"
    : "space-y-4 text-sm leading-relaxed text-zo-text-secondary";

  return (
    <div className={stackClass}>
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          const className = compact
            ? "text-[14px] font-semibold text-zo-text"
            : block.level <= 3
              ? "font-heading text-sm font-bold uppercase tracking-wide text-foreground"
              : "text-sm font-bold text-foreground";
          return (
            <h5 key={index} className={className}>
              {renderInline(block.text, highlights)}
            </h5>
          );
        }

        if (block.type === "table") {
          return (
            <ProposalTable
              key={index}
              blockIndex={index}
              headers={block.headers}
              rows={block.rows}
              compact
              highlights={highlights}
            />
          );
        }

        if (block.type === "list") {
          const ListTag = block.ordered ? "ol" : "ul";
          return (
            <ListTag
              key={index}
              className={`pl-5 ${
                block.ordered ? "list-decimal" : "list-disc"
              } ${compact ? "space-y-1" : "space-y-1.5"}`}
            >
              {block.items.map((item, itemIndex) => (
                <li key={`${index}-li-${itemIndex}`}>
                  {renderInline(item ?? "", highlights)}
                </li>
              ))}
            </ListTag>
          );
        }

        if (block.type === "designer_note") {
          return (
            <div
              key={index}
              className="proposal-designer-note-callout"
              role="note"
            >
              <p className="proposal-designer-note-label">Designer note</p>
              <p className="proposal-designer-note-body">
                {renderInline(block.text ?? "", highlights)}
              </p>
            </div>
          );
        }

        if (block.type === "gap_callout") {
          if (isInternalScanTag(block.tag)) return null;
          return (
            <GapCallout
              key={index}
              tag={block.tag}
              highlighted={tagMatchesHighlight(block.tag, highlights)}
            />
          );
        }

        if (block.type === "hr") {
          return (
            <hr
              key={index}
              className={compact ? "my-2 border-zo-border" : "my-4 border-zo-border"}
              aria-hidden
            />
          );
        }

        if (block.type === "paragraph") {
          return (
            <p key={index}>{renderInline(block.text, highlights)}</p>
          );
        }

        return null;
      })}
    </div>
  );
}
