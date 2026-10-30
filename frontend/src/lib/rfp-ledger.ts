/** Pure helpers for the RFP activity ledger UI. */

export type DiffSegment = { type: "same" | "add" | "del"; text: string };

/** Above this many words on either side, skip the word-level diff. */
export const MAX_DIFF_WORDS = 3000;

function tokens(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [];
}

function push(out: DiffSegment[], type: DiffSegment["type"], text: string) {
  const last = out[out.length - 1];
  if (last && last.type === type) last.text += text;
  else out.push({ type, text });
}

/** Word-level diff (longest common subsequence). Deletions come before additions. */
export function wordDiff(before: string, after: string): DiffSegment[] {
  const a = tokens(before);
  const b = tokens(after);
  if (a.length === 0 && b.length === 0) return [];
  if (a.length > MAX_DIFF_WORDS || b.length > MAX_DIFF_WORDS) {
    return [
      { type: "del", text: before },
      { type: "add", text: after },
    ];
  }
  const keyA = a.map((t) => t.trim());
  const keyB = b.map((t) => t.trim());
  const width = b.length + 1;
  const lcs = new Uint16Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i * width + j] =
        keyA[i] === keyB[j]
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1]);
    }
  }
  const out: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (keyA[i] === keyB[j]) {
      push(out, "same", b[j]);
      i++;
      j++;
    } else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) {
      push(out, "del", a[i]);
      i++;
    } else {
      push(out, "add", b[j]);
      j++;
    }
  }
  // Keep deletions ahead of additions at the tail, matching the loop above.
  while (i < a.length) push(out, "del", a[i++]);
  while (j < b.length) push(out, "add", b[j++]);
  return out;
}

export function bucketLabel(bucket: string): string {
  switch (bucket) {
    case "manual":
      return "Manual";
    case "ai":
      return "AI-assisted";
    case "restore":
      return "Restore/reset";
    case "other":
      return "Other";
    default:
      return bucket;
  }
}

const SOURCE_LABELS: Record<string, string> = {
  manual_edit: "Typed edit",
  personas: "Key personas",
  chat_apply: "Chat edit",
  chat_confirm: "Chat edit (confirmed)",
  restore_snapshot: "Restored a snapshot",
  restore_archive: "Restored an archive",
  restart: "Restarted from an earlier step",
  reset: "Reset draft",
  other: "Other edit",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.split("_").join(" ");
}

export function outcomeLabel(outcome: string): string {
  switch (outcome) {
    case "running":
      return "Running";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Stopped";
    default:
      return outcome;
  }
}

export function formatWordDelta(added: number, removed: number): string {
  if (!added && !removed) return "no change";
  return `+${added} / −${removed}`;
}

export function formatCost(usd: number): string {
  return usd > 0 ? `$${usd.toFixed(2)}` : "—";
}

export function prettyPhase(phase: string): string {
  return phase.split("-").join(" ");
}
