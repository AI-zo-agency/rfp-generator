export type DiffLine = { kind: "same" | "added" | "removed"; text: string };

const MAX_CELLS = 4_000_000;

/** Line by line comparison. Common start and end are trimmed first so long prompts stay cheap. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");

  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }

  const head = a.slice(0, start).map((text): DiffLine => ({ kind: "same", text }));
  const tail = a.slice(endA).map((text): DiffLine => ({ kind: "same", text }));
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);

  if (midA.length * midB.length > MAX_CELLS) {
    return [
      ...head,
      ...midA.map((text): DiffLine => ({ kind: "removed", text })),
      ...midB.map((text): DiffLine => ({ kind: "added", text })),
      ...tail,
    ];
  }

  // Longest common subsequence table, filled from the end.
  const n = midA.length;
  const m = midB.length;
  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = midA[i] === midB[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const middle: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (midA[i] === midB[j]) {
      middle.push({ kind: "same", text: midA[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      middle.push({ kind: "removed", text: midA[i++] });
    } else {
      middle.push({ kind: "added", text: midB[j++] });
    }
  }
  while (i < n) middle.push({ kind: "removed", text: midA[i++] });
  while (j < m) middle.push({ kind: "added", text: midB[j++] });

  return [...head, ...middle, ...tail];
}

export function countChanges(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "added") added++;
    else if (line.kind === "removed") removed++;
  }
  return { added, removed };
}
