import type { ProposalOutline } from "@/types/proposal";

const MANUAL_FILL_CELL =
  "[MANUAL FILL: Sonja — verified contact from ClientList/KB]";

const LAYOUT_INSTRUCTION_RE =
  /^(?:single\s+)?\d[\s-]*column\s+table\b|one\s+column\s+per\s+reference|no\s+additional\s+layout\s+needed/i;

const FIELD_LABEL_ONLY_RE =
  /^(?:\*\*)?(?:contact|phone|email|title|organization|name|reference\s+contact|contact\s+info)(?:\*\*)?\s*:?\s*$/i;

const LIST_ITEM_RE = /^\s*(?:\d{1,2}[.)]\s+|[-*•]\s+)(.+?)\s*$/;

function isReferenceTableHeader(line: string): boolean {
  if (!line.trim().startsWith("|")) return false;
  const low = line.toLowerCase();
  return (
    low.includes("contact") ||
    low.includes("organization") ||
    low.includes("phone") ||
    low.includes("email") ||
    low.includes("engagement") ||
    low.includes("client") ||
    low.includes("field")
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

function stripLayoutInstructions(text: string): string {
  return text
    .split("\n")
    .filter((ln) => !LAYOUT_INSTRUCTION_RE.test(ln.trim()))
    .join("\n")
    .trim();
}

function wantsColumnPerReference(text: string): boolean {
  return /column\s+per\s+reference|one\s+column\s+per\s+reference|\d[\s-]*column\b.{0,48}reference/i.test(
    text
  );
}

function engagementsFromList(body: string): string[] {
  const items: string[] = [];
  const seen = new Set<string>();
  for (const line of body.split("\n")) {
    const m = line.match(LIST_ITEM_RE);
    if (!m) continue;
    let raw = m[1].trim();
    if (FIELD_LABEL_ONLY_RE.test(raw)) continue;
    if (/^\[(?:MANUAL\s+FILL|VERIFY)\b/i.test(raw)) continue;
    if (/^(contact|phone|email)\s*:/i.test(raw)) continue;
    raw = raw.split(/\bContact\s*:/i)[0].trim();
    raw = raw.replace(/\*+/g, "").replace(/^[-—–\s]+|[-—–\s]+$/g, "").trim();
    if (raw.length < 4) continue;
    const key = raw.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(raw.slice(0, 140));
    if (items.length >= 6) break;
  }
  return items;
}

function hasUsableReferenceTable(text: string): boolean {
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    if (!isReferenceTableHeader(lines[i])) continue;
    let j = i + 1;
    if (j < lines.length && isTableSeparator(lines[j].trim())) j += 1;
    while (j < lines.length && !lines[j].trim()) j += 1;
    if (j < lines.length && lines[j].trim().startsWith("|")) return true;
  }
  return false;
}

function buildRowTable(engagements: string[]): string {
  const rows = engagements.map(
    (eng) =>
      `| ${eng} | Relevant past performance for this RFP | ${MANUAL_FILL_CELL} | ${MANUAL_FILL_CELL} |`
  );
  return [
    "| Client / Engagement | Scope / Relevance | Reference Contact | Contact Info |",
    "|---|---|---|---|",
    ...rows,
  ].join("\n");
}

function buildColumnTable(engagements: string[], columns = 4): string {
  const n = Math.max(2, Math.min(columns, 6));
  const refs = [...engagements];
  while (refs.length < n) {
    refs.push("[MANUAL FILL: Sonja — client / engagement from ClientList/KB]");
  }
  const header =
    "| Field | " +
    Array.from({ length: n }, (_, i) => `Reference ${i + 1}`).join(" | ") +
    " |";
  const sep = "|---|" + Array.from({ length: n }, () => "---").join("|") + "|";
  const client = "| Client / Engagement | " + refs.slice(0, n).join(" | ") + " |";
  const contact =
    "| Reference Contact | " +
    Array.from({ length: n }, () => MANUAL_FILL_CELL).join(" | ") +
    " |";
  const info =
    "| Phone / Email | " +
    Array.from({ length: n }, () => MANUAL_FILL_CELL).join(" | ") +
    " |";
  return [header, sep, client, contact, info].join("\n");
}

/** Rebuild reference tables when a header row sits above a bullet list. */
export function repairReferenceTableMarkdown(text: string): string {
  if (!text) return text;
  const cleaned = stripLayoutInstructions(text);
  if (!cleaned.includes("|")) return cleaned;
  const lines = cleaned.split("\n");
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

/** Convert list-shaped References into a markdown table (display + persist repair). */
export function repairListShapedReferencesMarkdown(
  text: string,
  options?: { originalForLayoutHint?: string }
): string {
  const raw = text || "";
  const cleaned = stripLayoutInstructions(raw);
  if (hasUsableReferenceTable(cleaned)) {
    return cleaned === raw ? raw : cleaned;
  }
  const engagements = engagementsFromList(cleaned);
  if (engagements.length === 0) {
    return cleaned === raw ? raw : cleaned;
  }
  const hint = options?.originalForLayoutHint || raw;
  const table = wantsColumnPerReference(hint)
    ? buildColumnTable(engagements)
    : buildRowTable(engagements);

  const lead: string[] = [];
  for (const block of cleaned.split(/\n\s*\n/)) {
    const chunk = block.trim();
    if (!chunk) continue;
    if (LIST_ITEM_RE.test(chunk.split("\n")[0] || "")) continue;
    if (FIELD_LABEL_ONLY_RE.test(chunk)) continue;
    if (/^\[(?:MANUAL\s+FILL|VERIFY)\b/i.test(chunk)) continue;
    if (chunk.startsWith("|")) continue;
    lead.push(chunk);
    if (lead.length >= 2) break;
  }
  return [...lead, table].filter(Boolean).join("\n\n") + "\n";
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
      const original = s.content || "";
      let repaired = repairReferenceTableMarkdown(original);
      repaired = repairListShapedReferencesMarkdown(repaired, {
        originalForLayoutHint: original,
      });
      if (repaired === original) return s;
      return { ...s, content: repaired };
    }),
  };
}
