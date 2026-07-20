type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; text: string }
  | { type: "designer_note"; text: string }
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

/** Strip internal KB evidence markers ([E1], [E2], …) from client-facing copy. */
export function stripEvidenceCitations(text: string): string {
  return (text || "").replace(/\s*\[E\d+\]/g, "");
}

function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  const cells = trimmed
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
  return cells.length >= 2;
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  // GFM alignment row: | --- | :---: | ---: |
  return /^\|?[\s:\-–—]+\|[\s|:\-–—]+\|?$/.test(trimmed);
}

function parseTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function parseBlocks(body: string): Block[] {
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const trimmed = line.trim();

    if (!trimmed) {
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
      while (index < lines.length && isTableRow(lines[index]?.trim() ?? "")) {
        tableLines.push(lines[index]!.trim());
        index += 1;
      }
      const dataLines = tableLines.filter((row) => !isTableSeparator(row));
      if (dataLines.length > 0) {
        const headers = parseTableRow(dataLines[0]);
        const rows = dataLines.slice(1).map(parseTableRow);
        blocks.push({ type: "table", headers, rows });
      }
      continue;
    }

    if (/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      const ordered = /^\d+\.\s+/.test(trimmed);
      const items: string[] = [];
      while (index < lines.length) {
        const current = lines[index]?.trim() ?? "";
        if (!current) break;
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
  const m = text.match(/^\[(?:DESIGNER NOTE|Designer Note)\s*:?\s*([\s\S]*)\]\s*$/i);
  return m ? m[1].trim() : null;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildInlinePattern(highlightTexts: string[]): RegExp {
  const tagPattern = String.raw`\*\*[^*]+\*\*|\[(?:VERIFY|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER)[^\]]*\]`;
  const unique = [...new Set(highlightTexts.map((h) => h.trim()).filter(Boolean))].sort(
    (a, b) => b.length - a.length
  );
  if (unique.length === 0) {
    return new RegExp(`(${tagPattern})`, "gi");
  }
  const highlights = unique.map(escapeRegex).join("|");
  return new RegExp(`(${highlights}|${tagPattern})`, "gi");
}

function renderInline(text: string | undefined | null, highlightTexts: string[] = []) {
  let markAssigned = false;
  const safe = text ?? "";
  const parts = safe.split(buildInlinePattern(highlightTexts));
  const normalizedHighlights = new Set(
    highlightTexts.map((h) => h.trim()).filter(Boolean)
  );

  return parts.map((part, index) => {
    if (!part) return null;

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
      return (
        <span
          key={index}
          className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-semibold text-red-800"
          title="Needs manual confirmation before submit"
        >
          {part}
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
  variant?: "report" | "document";
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
              <div key={index} className="my-4 overflow-x-auto rounded-xl border border-zo-border bg-white">
                <table className="w-full min-w-[520px] text-left text-[13px]">
                  <thead>
                    <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-xs uppercase tracking-wide text-zo-text-muted">
                      {block.headers.map((header, headerIndex) => (
                        <th key={`${index}-h-${headerIndex}`} className="px-4 py-2.5 font-bold">
                          {renderInline(header, highlights)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex} className="border-b border-zo-border/60 align-top last:border-0">
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex} className="px-4 py-3">
                            {renderInline(cell, highlights)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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

          return <p key={index}>{renderInline(block.text ?? "", highlights)}</p>;
        })}
      </div>
    );
  }

  return (
    <div className="space-y-4 text-sm leading-relaxed text-zo-text-secondary">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          const className =
            block.level <= 3
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
            <div key={index} className="overflow-x-auto rounded-lg border border-zo-border">
              <table className="w-full min-w-[520px] text-left text-sm">
                <thead>
                  <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-xs uppercase tracking-wide text-zo-text-muted">
                    {block.headers.map((header, headerIndex) => (
                      <th key={`${index}-h-${headerIndex}`} className="px-3 py-2 font-bold">
                        {renderInline(header, highlights)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr
                      key={rowIndex}
                      className="border-b border-zo-border/60 align-top last:border-0"
                    >
                      {row.map((cell, cellIndex) => (
                        <td
                          key={cellIndex}
                          className="px-3 py-2.5 text-zo-text-secondary"
                        >
                          {renderInline(cell, highlights)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        if (block.type === "list") {
          const ListTag = block.ordered ? "ol" : "ul";
          return (
            <ListTag
              key={index}
              className={`space-y-1.5 pl-5 ${
                block.ordered ? "list-decimal" : "list-disc"
              }`}
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

        return (
          <p key={index}>{renderInline(block.text ?? "", highlights)}</p>
        );
      })}
    </div>
  );
}
