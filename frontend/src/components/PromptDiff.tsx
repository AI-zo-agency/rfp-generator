"use client";

import { useMemo } from "react";
import { countChanges, diffLines, type DiffLine } from "@/lib/line-diff";

const CONTEXT = 2;

type Row = DiffLine | { kind: "gap"; hidden: number };

/** Keep changed lines and a little around them; fold long unchanged stretches. */
function fold(lines: DiffLine[]): Row[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((line, index) => {
    if (line.kind === "same") return;
    for (let k = Math.max(0, index - CONTEXT); k <= Math.min(lines.length - 1, index + CONTEXT); k++) keep[k] = true;
  });
  const rows: Row[] = [];
  let hidden = 0;
  lines.forEach((line, index) => {
    if (keep[index]) {
      if (hidden) rows.push({ kind: "gap", hidden });
      hidden = 0;
      rows.push(line);
    } else {
      hidden++;
    }
  });
  if (hidden) rows.push({ kind: "gap", hidden });
  return rows;
}

export function changeSummary(before: string, after: string): string {
  const { added, removed } = countChanges(diffLines(before, after));
  if (!added && !removed) return "No difference";
  const part = (n: number, word: string) => `${n} ${n === 1 ? "line" : "lines"} ${word}`;
  return [added ? part(added, "added") : "", removed ? part(removed, "removed") : ""].filter(Boolean).join(", ");
}

export function PromptDiff({ before, after }: { before: string; after: string }) {
  const rows = useMemo(() => fold(diffLines(before, after)), [before, after]);

  if (!rows.some((row) => row.kind === "added" || row.kind === "removed")) {
    return <p className="text-[15px] text-zo-text-muted">There is no difference.</p>;
  }

  return (
    <div
      className="max-h-80 overflow-auto rounded-lg border border-zo-border bg-white text-sm leading-6"
      role="group"
      aria-label="What changed"
    >
      {rows.map((row, index) =>
        row.kind === "gap" ? (
          <p key={index} className="bg-[#f6f7f9] px-4 py-1 text-xs text-zo-text-muted">
            {row.hidden} unchanged {row.hidden === 1 ? "line" : "lines"}
          </p>
        ) : (
          <p
            key={index}
            className={`flex gap-3 whitespace-pre-wrap break-words px-4 py-0.5 ${
              row.kind === "added"
                ? "bg-green-50 text-green-900"
                : row.kind === "removed"
                  ? "bg-red-50 text-red-900 line-through decoration-red-300"
                  : "text-zo-text-secondary"
            }`}
          >
            <span aria-hidden className="w-[4.5rem] shrink-0 select-none whitespace-nowrap font-semibold">
              {row.kind === "added" ? "Added" : row.kind === "removed" ? "Removed" : ""}
            </span>
            <span className="min-w-0 flex-1">{row.text || " "}</span>
          </p>
        ),
      )}
    </div>
  );
}
