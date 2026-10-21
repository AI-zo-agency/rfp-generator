import { marked } from "marked";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

marked.setOptions({
  gfm: true,
  breaks: false,
});

const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-",
  emDelimiter: "*",
  strongDelimiter: "**",
});
turndown.use(gfm);
// Keep proposal gap tags / citations as literal text, not HTML entities mess.
turndown.escape = (str: string) => str;

const TABLE_HTML_RE =
  /<\/?t(?:able|head|body|r|d|h)\b|<\/?colgroup\b|<\/?col\b|<\/?p\b|<strong\b|<\/strong>|<br\s*\/?>/i;

/**
 * TipTap emits tables as <tbody> + <th>/<td><p>…</p> + <colgroup> + styles.
 * turndown-plugin-gfm only handles clean thead tables, so preprocess first.
 */
export function normalizeTipTapTableHtml(html: string): string {
  if (typeof document === "undefined") {
    return normalizeTipTapTableHtmlNode(html);
  }
  const wrap = document.createElement("div");
  wrap.innerHTML = html;
  normalizeTablesInElement(wrap);
  return wrap.innerHTML;
}

/** JSDOM-free path for unit tests / SSR — string transforms that match TipTap output. */
function normalizeTipTapTableHtmlNode(html: string): string {
  let out = html;
  // Drop empty nested tables TipTap inserts inside cells.
  out = out.replace(
    /<table\b[^>]*>\s*<tbody>\s*(?:<tr>\s*(?:<t[dh]\b[^>]*>\s*(?:<p>\s*<\/p>\s*)*<\/t[dh]>\s*)+<\/tr>\s*)+<\/tbody>\s*<\/table>/gi,
    "",
  );
  // Strip colgroup (breaks GFM turndown).
  out = out.replace(/<colgroup\b[^>]*>[\s\S]*?<\/colgroup>/gi, "");
  // Drop TipTap sizing attrs that confuse turndown into keeping raw HTML.
  out = out.replace(/\s(?:style|class|colspan|rowspan)="[^"]*"/gi, "");
  // Unwrap single <p> wrappers inside cells → plain cell text.
  out = out.replace(
    /<(t[dh])(\b[^>]*)>\s*<p>([\s\S]*?)<\/p>\s*<\/\1>/gi,
    "<$1$2>$3</$1>",
  );
  // Collapse remaining multi-paragraph cells to <br>-joined text.
  out = out.replace(
    /<(t[dh])(\b[^>]*)>((?:<p>[\s\S]*?<\/p>\s*)+)<\/\1>/gi,
    (_m, tag, attrs, inner: string) => {
      const text = inner
        .replace(/<\/?p>/gi, (p) => (/<\/p>/i.test(p) ? "<br>" : ""))
        .replace(/(<br>\s*)+$/i, "")
        .trim();
      return `<${tag}${attrs}>${text}</${tag}>`;
    },
  );
  // Promote first header row (all <th>) into <thead> so turndown GFM fires.
  out = out.replace(
    /<table(\b[^>]*)>\s*<tbody>\s*(<tr>\s*(?:<th\b[^>]*>[\s\S]*?<\/th>\s*)+<\/tr>)\s*/gi,
    "<table$1><thead>$2</thead><tbody>",
  );
  return out;
}

function normalizeTablesInElement(root: HTMLElement): void {
  const tables = Array.from(root.querySelectorAll("table")).reverse();
  for (const table of tables) {
    const text = (table.textContent || "").replace(/\u00a0/g, " ").trim();
    if (!text && table.parentElement && table.parentElement.closest("table")) {
      table.remove();
      continue;
    }

    table.querySelectorAll("colgroup").forEach((el) => el.remove());
    table.removeAttribute("style");
    table.removeAttribute("class");

    table.querySelectorAll("th, td").forEach((cell) => {
      cell.removeAttribute("style");
      cell.removeAttribute("class");
      cell.removeAttribute("colspan");
      cell.removeAttribute("rowspan");
      const paragraphs = Array.from(cell.querySelectorAll(":scope > p"));
      if (paragraphs.length === 0) return;
      const parts = paragraphs.map((p) => p.innerHTML.trim()).filter(Boolean);
      cell.innerHTML = parts.join("<br>");
    });

    const tbody = table.querySelector("tbody");
    const firstRow = tbody?.querySelector(":scope > tr");
    if (
      tbody &&
      firstRow &&
      !table.querySelector("thead") &&
      firstRow.querySelectorAll(":scope > th").length > 0 &&
      firstRow.querySelectorAll(":scope > td").length === 0
    ) {
      const thead = table.ownerDocument!.createElement("thead");
      thead.appendChild(firstRow);
      table.insertBefore(thead, tbody);
    }
  }
}

/** Convert a standalone HTML table (or fragment of rows) into GFM pipes. */
function tableHtmlToGfm(tableHtml: string): string {
  const wrapped = /<table\b/i.test(tableHtml)
    ? tableHtml
    : `<table><tbody>${tableHtml}</tbody></table>`;
  const normalized = normalizeTipTapTableHtml(wrapped);
  let md = turndown.turndown(normalized).trim();
  if (!TABLE_HTML_RE.test(md) && md.includes("|")) return md;

  // Deterministic fallback when turndown still leaves tags / fails.
  const rows = extractHtmlTableRows(normalized);
  if (rows.length === 0) return stripInlineHtml(md);
  return rowsToGfm(rows);
}

function extractHtmlTableRows(html: string): string[][] {
  const rows: string[][] = [];
  const trRe = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
  let trMatch: RegExpExecArray | null;
  while ((trMatch = trRe.exec(html)) !== null) {
    const cells: string[] = [];
    const cellRe = /<t[dh]\b[^>]*>([\s\S]*?)<\/t[dh]>/gi;
    let cellMatch: RegExpExecArray | null;
    while ((cellMatch = cellRe.exec(trMatch[1])) !== null) {
      cells.push(stripInlineHtml(cellMatch[1]).replace(/\s+/g, " ").trim());
    }
    // Keep fully blank rows (TipTap Add row) — Preview must show them.
    if (cells.length > 0) rows.push(cells);
  }
  return rows;
}

function rowsToGfm(rows: string[][]): string {
  if (rows.length === 0) return "";
  const width = Math.max(...rows.map((r) => r.length));
  const pad = (row: string[]) =>
    Array.from({ length: width }, (_, i) => (row[i] ?? "").replace(/\|/g, "\\|"));
  const header = pad(rows[0]);
  const body = rows.slice(1).map(pad);
  const sep = header.map(() => "---");
  return [
    `| ${header.join(" | ")} |`,
    `| ${sep.join(" | ")} |`,
    ...body.map((r) => `| ${r.join(" | ")} |`),
  ].join("\n");
}

function stripInlineHtml(text: string): string {
  return (text ?? "")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<\/?strong>/gi, "**")
    .replace(/<\/?b>/gi, "**")
    .replace(/<\/?em>/gi, "*")
    .replace(/<\/?i>/gi, "*")
    .replace(/<\/?p>/gi, " ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Rebuild a pipe-table line that got TipTap HTML concatenated into a cell, e.g.
 * `| A1 | Brand | $4,800</td></tr><tr><td><p>A2</p></td>...`
 */
function repairCorruptedPipeTableBlock(block: string): string {
  const htmlStart = block.search(/<\/?t[rdh]\b|<table\b/i);
  if (htmlStart < 0) return block;

  const before = block.slice(0, htmlStart).trimEnd();
  let fragment = block.slice(htmlStart).trim();
  // Splice landed after a finished cell: `…$4,800</td></tr><tr>…`
  // Drop the closing leftovers so we only parse the following rows.
  const splicedAfterCompleteCell = /^<\/t[dh]>/i.test(fragment);
  if (splicedAfterCompleteCell) {
    fragment = fragment
      .replace(/^<\/t[dh]>\s*/i, "")
      .replace(/^<\/tr>\s*/i, "");
  }

  if (!/<table\b/i.test(fragment)) {
    if (!/^<tr\b/i.test(fragment)) {
      fragment = `<tr>${fragment}</tr>`;
    }
    fragment = `<table><tbody>${fragment}</tbody></table>`;
  }

  const beforeLines = before
    .split("\n")
    .map((l) => l.trimEnd())
    .filter((l) => l.length > 0);

  if (beforeLines.length === 0) {
    return tableHtmlToGfm(fragment) || stripInlineHtml(block);
  }

  const completeBefore = beforeLines.slice(0, -1);
  const partialLast = beforeLines[beforeLines.length - 1];
  const leadingCells = partialLast
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
  while (
    leadingCells.length > 0 &&
    leadingCells[leadingCells.length - 1] === ""
  ) {
    leadingCells.pop();
  }

  const recoveredRows = extractHtmlTableRows(
    normalizeTipTapTableHtml(fragment),
  );

  // If HTML continued mid-cell (started with <td> not </td>), merge into first row.
  if (
    !splicedAfterCompleteCell &&
    recoveredRows.length > 0 &&
    leadingCells.length > 0
  ) {
    recoveredRows[0] = [
      ...leadingCells,
      ...recoveredRows[0].filter((c) => c.length > 0),
    ];
  } else if (leadingCells.length > 0) {
    recoveredRows.unshift(leadingCells);
  }

  const headerSource = completeBefore[0];
  let headers: string[] = [];
  let bodyStart = 0;
  if (headerSource && headerSource.includes("|")) {
    headers = headerSource
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
    bodyStart = 1;
    if (completeBefore[1] && /^\|?\s*:?-{3,}/.test(completeBefore[1])) {
      bodyStart = 2;
    }
  }

  const priorBody = completeBefore.slice(bodyStart).map((line) =>
    line
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim()),
  );

  const allRows =
    headers.length > 0
      ? [headers, ...priorBody, ...recoveredRows]
      : [...priorBody, ...recoveredRows];

  if (allRows.length === 0) return stripInlineHtml(block);
  return rowsToGfm(allRows);
}

/** TipTap table cells require block nodes — wrap bare th/td text in <p>. */
function wrapTableCellsInParagraphs(html: string): string {
  return html.replace(
    /<(t[dh])(\b[^>]*)>([\s\S]*?)<\/\1>/gi,
    (_m, tag: string, attrs: string, inner: string) => {
      const trimmed = inner.trim();
      if (!trimmed) return `<${tag}${attrs}><p></p></${tag}>`;
      if (/^<p[\s>]/i.test(trimmed)) return `<${tag}${attrs}>${inner}</${tag}>`;
      return `<${tag}${attrs}><p>${trimmed}</p></${tag}>`;
    },
  );
}

/** Convert section markdown → HTML for TipTap setContent. */
export function markdownToHtml(markdown: string): string {
  const repaired = repairLeakedHtmlInMarkdown(markdown ?? "");
  const raw = repaired.trim();
  if (!raw) return "<p></p>";
  const parsed = marked.parse(raw, { async: false });
  const html = typeof parsed === "string" ? parsed : String(parsed);
  return wrapTableCellsInParagraphs(html);
}

/** Convert TipTap HTML → markdown for section.content persistence. */
export function htmlToMarkdown(html: string): string {
  const raw = (html ?? "").trim();
  if (!raw || raw === "<p></p>") return "";

  const tables: string[] = [];
  const withPlaceholders = normalizeTipTapTableHtml(raw).replace(
    /<table\b[\s\S]*?<\/table>/gi,
    (tableHtml) => {
      const idx = tables.length;
      tables.push(tableHtmlToGfm(tableHtml));
      return `<p>ZO_TABLE_PLACEHOLDER_${idx}</p>`;
    },
  );
  let md = turndown.turndown(withPlaceholders).trim();
  tables.forEach((gfmTable, i) => {
    md = md.replace(
      new RegExp(`ZO_TABLE_PLACEHOLDER_${i}`, "g"),
      `\n\n${gfmTable}\n\n`,
    );
  });
  return repairLeakedHtmlInMarkdown(md).replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * If TipTap HTML leaked into markdown (full tables OR leftover td/tr fragments
 * inside pipe rows), convert back to clean GFM so Preview renders again.
 */
export function repairLeakedHtmlInMarkdown(markdown: string): string {
  const src = markdown ?? "";
  if (!TABLE_HTML_RE.test(src)) return src;

  // 1) Convert complete <table>…</table> blocks.
  let out = src.replace(/<table\b[\s\S]*?<\/table>/gi, (tableHtml) =>
    tableHtmlToGfm(tableHtml),
  );

  // 2) Pipe-table blocks that still contain HTML fragments in a cell.
  if (TABLE_HTML_RE.test(out)) {
    out = out.replace(
      /(^|\n)((?:\|[^\n]*(?:\n|$))+)/g,
      (full, lead: string, block: string) => {
        if (!TABLE_HTML_RE.test(block)) return full;
        const repaired = repairCorruptedPipeTableBlock(block.trimEnd());
        return `${lead}${repaired}\n`;
      },
    );
  }

  // 3) Last resort: strip any remaining tags so Preview never shows raw HTML.
  if (TABLE_HTML_RE.test(out)) {
    out = out
      .split("\n")
      .map((line) => (TABLE_HTML_RE.test(line) ? stripInlineHtml(line) : line))
      .join("\n");
  }

  return out.replace(/\n{3,}/g, "\n\n").trimEnd();
}

/** Loose compare so heading/list spacing differences don't fail round-trip tests. */
export function normalizeMarkdownForCompare(md: string): string {
  return (md ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/\|[ \t]+/g, "| ")
    .replace(/[ \t]+\|/g, " |")
    .trim()
    .toLowerCase();
}
