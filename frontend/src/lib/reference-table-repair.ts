import type { ProposalOutline } from "@/types/proposal";

const MANUAL_FILL_CELL =
  "[MANUAL FILL: Sonja — verified contact from ClientList/KB]";

function isReferenceTableHeader(line: string): boolean {
  if (!line.trim().startsWith("|")) return false;
  const low = line.toLowerCase();
  return (
    low.includes("contact") ||
    low.includes("organization") ||
    low.includes("phone") ||
    low.includes("email")
  );
}

function isTableSeparator(line: string): boolean {
  const cells = line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
  return (
    cells.length > 0 &&
    cells.every((c) => !c || /^:?-{2,}:?$/.test(c))
  );
}

function isBulletRow(line: string): boolean {
  const t = line.trim();
  return (
    t.startsWith("-") ||
    t.startsWith("*") ||
    t.startsWith("[MANUAL FILL")
  );
}

/** Rebuild reference tables when a header row sits above a bullet list. */
export function repairReferenceTableMarkdown(text: string): string {
  if (!text || !text.includes("|")) return text;
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (!isReferenceTableHeader(stripped)) {
      out.push(lines[i]);
      i += 1;
      continue;
    }

    const header = lines[i];
    const width = stripped.replace(/^\|/, "").replace(/\|$/, "").split("|").length;
    let j = i + 1;
    let sep: string | null = null;
    if (j < lines.length && isTableSeparator(lines[j].trim())) {
      sep = lines[j];
      j += 1;
    }
    while (j < lines.length && !lines[j].trim()) j += 1;

    if (j < lines.length && lines[j].trim().startsWith("|")) {
      out.push(lines[i]);
      i += 1;
      continue;
    }

    const bullets: string[] = [];
    let k = j;
    while (k < lines.length) {
      const row = lines[k].trim();
      if (!row) {
        k += 1;
        continue;
      }
      if (!isBulletRow(row)) break;
      bullets.push(row);
      k += 1;
    }

    if (bullets.length === 0) {
      out.push(lines[i]);
      i += 1;
      continue;
    }

    out.push(header);
    if (sep) out.push(sep);
    for (let idx = 0; idx < bullets.length; idx += 1) {
      let org = bullets[idx];
      if (org.startsWith("-") || org.startsWith("*")) {
        org = org.replace(/^[-*]\s+/, "");
        org = org.split("—")[0].split(" - ")[0].trim();
        org = org.replace(/\*+/g, "").trim();
      }
      const cells = Array.from({ length: width }, () => MANUAL_FILL_CELL);
      if (width > 0) cells[0] = String(idx + 1);
      const orgCol = width >= 4 ? 3 : Math.max(0, width - 1);
      if (orgCol < width) cells[orgCol] = org;
      out.push(`| ${cells.join(" | ")} |`);
    }
    i = k;
  }

  return out.join("\n");
}

export function repairReferenceSectionsInOutline(
  draft: ProposalOutline
): ProposalOutline {
  return {
    ...draft,
    sections: draft.sections.map((s) => {
      const title = (s.title || "").toLowerCase();
      const sid = (s.id || "").toLowerCase();
      if (!title.includes("reference") && !sid.includes("reference")) {
        return s;
      }
      if (!s.content?.includes("|")) return s;
      const repaired = repairReferenceTableMarkdown(s.content);
      if (repaired === s.content) return s;
      return { ...s, content: repaired };
    }),
  };
}
