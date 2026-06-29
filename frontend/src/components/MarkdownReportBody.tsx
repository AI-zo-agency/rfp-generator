type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; text: string };

function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.includes("|") && trimmed.split("|").filter(Boolean).length >= 2;
}

function isTableSeparator(line: string): boolean {
  return /^\|?[\s:-]+\|[\s|:-]+\|?$/.test(line.trim());
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
    blocks.push({ type: "paragraph", text: paragraphLines.join(" ") });
  }

  return blocks;
}

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|\[VERIFY\]|\[FLAG[^\]]*\])/gi);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-semibold text-foreground">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (/^\[VERIFY\]$/i.test(part)) {
      return (
        <span
          key={index}
          className="rounded bg-zo-orange/15 px-1.5 py-0.5 text-xs font-semibold text-zo-orange"
        >
          [VERIFY]
        </span>
      );
    }
    if (/^\[FLAG/i.test(part)) {
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
}: {
  body: string;
  variant?: "report" | "document";
}) {
  const blocks = parseBlocks(body);

  if (variant === "document") {
    return (
      <div>
        {blocks.map((block, index) => {
          if (block.type === "heading") {
            if (block.level === 1) return <h1 key={index}>{renderInline(block.text)}</h1>;
            if (block.level === 2) return <h2 key={index}>{renderInline(block.text)}</h2>;
            if (block.level === 3) return <h3 key={index}>{renderInline(block.text)}</h3>;
            return <h4 key={index}>{renderInline(block.text)}</h4>;
          }

          if (block.type === "table") {
            return (
              <div key={index} className="my-4 overflow-x-auto rounded-xl border border-zo-border bg-white">
                <table className="w-full min-w-[520px] text-left text-[13px]">
                  <thead>
                    <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-xs uppercase tracking-wide text-zo-text-muted">
                      {block.headers.map((header) => (
                        <th key={header} className="px-4 py-2.5 font-bold">
                          {renderInline(header)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex} className="border-b border-zo-border/60 align-top last:border-0">
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex} className="px-4 py-3">
                            {renderInline(cell)}
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
                {block.items.map((item) => (
                  <li key={item}>{renderInline(item)}</li>
                ))}
              </ListTag>
            );
          }

          return <p key={index}>{renderInline(block.text)}</p>;
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
              {renderInline(block.text)}
            </h5>
          );
        }

        if (block.type === "table") {
          return (
            <div key={index} className="overflow-x-auto rounded-lg border border-zo-border">
              <table className="w-full min-w-[520px] text-left text-sm">
                <thead>
                  <tr className="border-b border-zo-border bg-[var(--zo-surface)] text-xs uppercase tracking-wide text-zo-text-muted">
                    {block.headers.map((header) => (
                      <th key={header} className="px-3 py-2 font-bold">
                        {renderInline(header)}
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
                          {renderInline(cell)}
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
              {block.items.map((item) => (
                <li key={item}>{renderInline(item)}</li>
              ))}
            </ListTag>
          );
        }

        return (
          <p key={index}>{renderInline(block.text)}</p>
        );
      })}
    </div>
  );
}
