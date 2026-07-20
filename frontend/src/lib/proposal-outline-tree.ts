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

const SECTION_1_ID_ORDER = [
  "section-1-who-we-are",
  "section-1-org-structure",
  "section-1-business-info",
  "section-1-certifications",
  "section-1-insurance",
  "section-1-company-overview",
] as const;

function parseTitleMajorMinor(title: string): { major: number; minor: number } {
  const m = title.match(/^\s*(\d+)\.(\d+)/);
  if (m) {
    return { major: parseInt(m[1], 10), minor: parseInt(m[2], 10) };
  }
  return { major: 999, minor: 999 };
}

/** Stable proposal order: 1.x company → 2.x bios → 3.x work → RFP tabs → other. */
export function compareManuscriptSections(
  a: OutlineSection,
  b: OutlineSection,
): number {
  const rank = (s: OutlineSection): [number, number, number, string] => {
    const id = s.id;
    const { major, minor } = parseTitleMajorMinor(s.title);

    if (id.startsWith("section-1-")) {
      const idx = SECTION_1_ID_ORDER.indexOf(
        id as (typeof SECTION_1_ID_ORDER)[number],
      );
      return [1, idx >= 0 ? idx : 40 + minor, minor, id];
    }
    if (
      id.startsWith("section-2-bio-") ||
      id === "section-2-team-overview"
    ) {
      return [2, minor, 0, id];
    }
    if (
      id.startsWith("section-3-work-") ||
      id === "section-3-our-work"
    ) {
      return [3, minor, 0, id];
    }
    if (id.startsWith("section-4-")) {
      return [4, major, minor, id];
    }
    if (id.startsWith("section-5-")) {
      return [5, major, minor, id];
    }
    if (s.source === "rfp" || id.startsWith("rfp-")) {
      return [6, major, minor, id];
    }
    return [7, major, minor, id];
  };

  const ra = rank(a);
  const rb = rank(b);
  for (let i = 0; i < ra.length; i++) {
    const av = ra[i];
    const bv = rb[i];
    if (av === bv) continue;
    if (typeof av === "string" && typeof bv === "string") {
      return av.localeCompare(bv);
    }
    return (av as number) - (bv as number);
  }
  return 0;
}

export function sortManuscriptSections(
  sections: OutlineSection[],
): OutlineSection[] {
  return [...sections].sort(compareManuscriptSections);
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
  const nodes: OutlineTreeNode[] = [];

  for (const { id: groupId, label } of GROUP_ORDER) {
    const children = sections.filter(
      (section) =>
        !used.has(section.id) &&
        !isPlaceholder(section) &&
        matchesGroup(section, groupId),
    );
    if (children.length === 0) continue;
    children.sort(compareManuscriptSections);
    children.forEach((section) => used.add(section.id));

    if (children.length === 1 && (groupId === "section-4" || groupId === "section-5")) {
      nodes.push({ kind: "leaf", section: children[0] });
      continue;
    }

    nodes.push({ kind: "group", id: groupId, label, sections: children });
  }

  const leftovers: OutlineSection[] = [];
  for (const section of sections) {
    if (used.has(section.id) || isPlaceholder(section)) continue;
    leftovers.push(section);
  }
  leftovers.sort(compareManuscriptSections);
  for (const section of leftovers) {
    nodes.push({ kind: "leaf", section });
  }

  return nodes;
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
