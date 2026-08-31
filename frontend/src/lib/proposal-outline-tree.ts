import type { OutlineSection } from "@/types/proposal";

const PLACEHOLDER_IDS = new Set([
  "section-2-bio-placeholder",
  "section-3-work-placeholder",
]);

const GROUP_ORDER: { id: string; label: string }[] = [
  { id: "section-1", label: "Section 1 — Company Overview" },
  { id: "section-2", label: "Section 2 — Team Bios" },
  { id: "section-3", label: "Section 3 — Our Work" },
  { id: "section-4", label: "Section 4 — Project Approach" },
  { id: "section-5", label: "Section 5 — Scope of Work" },
];

export type OutlineTreeGroup = {
  kind: "group";
  id: string;
  label: string;
  sections: OutlineSection[];
};

export type OutlineTreeLeaf = {
  kind: "leaf";
  section: OutlineSection;
};

export type OutlineTreeNode = OutlineTreeGroup | OutlineTreeLeaf;

export function isPlaceholderSection(section: OutlineSection): boolean {
  return PLACEHOLDER_IDS.has(section.id);
}

function isPlaceholder(section: OutlineSection): boolean {
  return isPlaceholderSection(section);
}

function matchesGroup(section: OutlineSection, groupId: string): boolean {
  const id = section.id;
  switch (groupId) {
    case "section-1":
      return id.startsWith("section-1-") || id === "section-1-company-overview";
    case "section-2":
      return (
        (id.startsWith("section-2-bio-") && id !== "section-2-bio-placeholder") ||
        id === "section-2-team-overview"
      );
    case "section-3":
      return (
        (id.startsWith("section-3-work-") && id !== "section-3-work-placeholder") ||
        id === "section-3-our-work"
      );
    case "section-4":
      return id.startsWith("section-4-");
    case "section-5":
      return id.startsWith("section-5-");
    default:
      return false;
  }
}

const OUTLINE_NUMBER_SEPARATORS = new Set(["—", "-", "–", ".", ":"]);

function isDigit(ch: string | undefined): boolean {
  return ch != null && ch >= "0" && ch <= "9";
}

/**
 * Strip a leading "3.2 ", "3.2 — ", "3.2: " style outline number off a title
 * that already carries the RFP's own numbering, so a display number can be
 * prepended without doubling up ("26. 3.2 Provide…" reading as two different
 * section numbers stapled together).
 *
 * Was a single leading-number regex — greedy backtracking bit it on exactly
 * the plain "3.2 Title" case (no dash after the number): the digit group took
 * "3", the optional dot-digit group took ".2", then it needed a separator
 * character and found none (next char is a letter) — so the regex engine
 * backtracked, gave back the ".2", and matched "3." instead (a bare "."
 * counts as a separator on its own). Result: "2 Provide…" — the minor number
 * survived as if it were the first word of the title. A plain left-to-right
 * scan has no backtracking to get wrong.
 */
export function stripLeadingOutlineNumber(title: string): string {
  const trimmed = (title || "").trim();
  const n = trimmed.length;

  let i = 0;
  while (isDigit(trimmed[i])) i++;
  if (i === 0) return trimmed; // no leading number at all

  if (trimmed[i] === "." && isDigit(trimmed[i + 1])) {
    i++;
    while (isDigit(trimmed[i])) i++;
  }

  let k = i;
  while (trimmed[k] === " " || trimmed[k] === "\t") k++;
  const hadGap = k > i;

  if (OUTLINE_NUMBER_SEPARATORS.has(trimmed[k])) {
    k++;
    while (trimmed[k] === " " || trimmed[k] === "\t") k++;
  } else if (!hadGap) {
    // Number runs straight into the next character with no space or
    // separator at all ("3.2Provide…") — not a real outline-number prefix.
    return trimmed;
  }

  const body = trimmed.slice(k).trim();
  return body || trimmed;
}

export function isZoStaticSubsection(section: Pick<OutlineSection, "id">): boolean {
  const id = section.id;
  return (
    id.startsWith("section-1-") ||
    id.startsWith("section-2-") ||
    id.startsWith("section-3-")
  );
}

function originalIndexMap(
  sections: OutlineSection[],
): Map<string, number> {
  return new Map(sections.map((section, index) => [section.id, index]));
}

/**
 * Manuscript order is whatever order the array is in — no fixed tier (company
 * → bios → work → RFP tabs) takes priority over it any more. That tier system
 * used to override drag-and-drop: a section could be moved in the array, but
 * this comparator silently sorted it back under its type's fixed slot every
 * render, so e.g. an RFP tab could never be dragged above Section 1. Grouping
 * (which sections visually cluster under "Section 1 — Company Overview" etc.)
 * is still decided by id prefix in buildOutlineSectionTree — that's a separate
 * concern from ordering.
 */
export function compareManuscriptSections(
  a: OutlineSection,
  b: OutlineSection,
  originalIndex?: ReadonlyMap<string, number>,
): number {
  const origOf = (id: string) => originalIndex?.get(id) ?? 0;
  const diff = origOf(a.id) - origOf(b.id);
  return diff !== 0 ? diff : a.id.localeCompare(b.id);
}

/**
 * Renumber "2.N — Name" / "3.N — Name" title prefixes to match array order.
 * Shared by delete and drag-reorder so the visible number never drifts from
 * where the section actually sits (Section 1 subsections carry no such
 * number, so they need no renumbering pass).
 */
export function renumberGroupedSectionTitles(
  sections: OutlineSection[],
): OutlineSection[] {
  let bio = 0;
  let work = 0;
  let changed = false;
  const next = sections.map((s) => {
    const isBio = s.id.startsWith("section-2-bio-") && s.id !== "section-2-bio-placeholder";
    const isWork = s.id.startsWith("section-3-work-") && s.id !== "section-3-work-placeholder";
    if (!isBio && !isWork) return s;
    const prefix = isBio ? ++bio : ++work;
    const group = isBio ? 2 : 3;
    const name = s.title.includes("—")
      ? s.title.split("—").slice(1).join("—").trim()
      : s.title;
    const title = `${group}.${prefix} — ${name}`;
    if (title === s.title) return s;
    changed = true;
    return { ...s, title };
  });
  return changed ? next : sections;
}

export function sortManuscriptSections(
  sections: OutlineSection[],
): OutlineSection[] {
  const originalIndex = originalIndexMap(sections);
  return [...sections].sort((a, b) =>
    compareManuscriptSections(a, b, originalIndex),
  );
}

export function normalizeOutlineSectionOrder(
  outline: { sections: OutlineSection[] },
): { sections: OutlineSection[] } {
  const sorted = sortManuscriptSections(outline.sections);
  const unchanged = sorted.every(
    (section, index) => section.id === outline.sections[index]?.id,
  );
  if (unchanged) return outline;
  return { ...outline, sections: sorted };
}

export function getManuscriptSections(sections: OutlineSection[]): OutlineSection[] {
  const filtered = sections.filter((section) => {
    if (isPlaceholder(section)) return false;
    if (section.content?.trim()) return true;
    // Keep static 1–3 stubs visible while drafting.
    if (section.id.startsWith("section-1-")) return true;
    if (section.id.startsWith("section-2-bio-")) return true;
    if (section.id.startsWith("section-3-work-")) return true;
    // Keep RFP/dynamic outline entries visible even before content lands.
    if (section.source === "rfp" || section.source === "generated") return true;
    return false;
  });
  return sortManuscriptSections(filtered);
}

/** 1-based index in reading order (Content tab, export, editor chrome). */
export function buildManuscriptIndexMap(
  sections: OutlineSection[],
): Map<string, number> {
  const map = new Map<string, number>();
  getManuscriptSections(sections).forEach((section, index) => {
    map.set(section.id, index + 1);
  });
  return map;
}

/** First real Our Work / Team Bios target for group-style nav clicks. */
export function resolveManuscriptJumpTarget(
  sections: OutlineSection[],
  requestedId: string,
): string {
  if (requestedId === "section-2-bio-placeholder" || requestedId === "section-2") {
    const firstBio = getManuscriptSections(sections).find((s) =>
      s.id.startsWith("section-2-bio-"),
    );
    if (firstBio) return firstBio.id;
  }
  if (requestedId === "section-3-work-placeholder" || requestedId === "section-3") {
    const firstWork = getManuscriptSections(sections).find((s) =>
      s.id.startsWith("section-3-work-"),
    );
    if (firstWork) return firstWork.id;
  }
  return requestedId;
}

export function buildOutlineSectionTree(
  sections: OutlineSection[],
): OutlineTreeNode[] {
  const used = new Set<string>();
  // (node, position) pairs, merged and sorted by position at the end — a
  // group's position is its first member's array slot, so dragging any RFP
  // tab (or any other leaf) above/below/between the template groups actually
  // moves it there, instead of groups always leading the list regardless of
  // where their members were dropped.
  const positioned: { node: OutlineTreeNode; pos: number }[] = [];
  const originalIndex = originalIndexMap(sections);
  const origOf = (id: string) => originalIndex.get(id) ?? 0;

  for (const { id: groupId, label } of GROUP_ORDER) {
    const children = sections.filter(
      (section) =>
        !used.has(section.id) &&
        !isPlaceholder(section) &&
        matchesGroup(section, groupId),
    );
    if (children.length === 0) continue;
    children.sort((a, b) => compareManuscriptSections(a, b, originalIndex));
    children.forEach((section) => used.add(section.id));
    const pos = Math.min(...children.map((s) => origOf(s.id)));

    if (children.length === 1 && (groupId === "section-4" || groupId === "section-5")) {
      positioned.push({ node: { kind: "leaf", section: children[0] }, pos });
      continue;
    }

    positioned.push({ node: { kind: "group", id: groupId, label, sections: children }, pos });
  }

  for (const section of sections) {
    if (used.has(section.id) || isPlaceholder(section)) continue;
    positioned.push({ node: { kind: "leaf", section }, pos: origOf(section.id) });
  }

  positioned.sort((a, b) => a.pos - b.pos);
  return positioned.map((entry) => entry.node);
}

/** RFP tabs continue 4, 5, 6… after static Sections 1–3. */
export function buildRfpTabDisplayNumbers(
  sections: OutlineSection[],
): Map<string, number> {
  const map = new Map<string, number>();
  let n = 4;
  for (const section of getManuscriptSections(sections)) {
    if (isZoStaticSubsection(section)) continue;
    if (section.id.startsWith("section-4-") || section.id.startsWith("section-5-")) {
      continue;
    }
    map.set(section.id, n);
    n += 1;
  }
  return map;
}

export function sectionListLabel(
  section: OutlineSection,
  rfpTabNumberById: ReadonlyMap<string, number>,
): string {
  const title = (section.title || "").trim();
  if (isZoStaticSubsection(section)) return title;
  const n = rfpTabNumberById.get(section.id);
  const body = stripLeadingOutlineNumber(title);
  if (n != null) return `${n}. ${body}`;
  return title;
}

export function groupContainsSection(
  group: OutlineTreeGroup,
  sectionId: string,
): boolean {
  return group.sections.some((section) => section.id === sectionId);
}

export function getTopLevelSectionProgress(sections: OutlineSection[]): {
  complete: number;
  total: number;
} {
  // Count real manuscript tabs (Sections 1–3 + RFP-mapped leaves), not the
  // fixed 5 template shells — otherwise RFP tabs can be drafted while the
  // header still shows 3/5 and 60%.
  const manuscript = getManuscriptSections(sections);
  if (manuscript.length === 0) {
    return { complete: 0, total: GROUP_ORDER.length };
  }
  const complete = manuscript.filter(
    (section) => section.content.trim().length > 0,
  ).length;
  return { complete, total: manuscript.length };
}
