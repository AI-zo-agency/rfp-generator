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

/** Sections rendered in Content tab + On this page nav (never placeholders). */
export function getManuscriptSections(sections: OutlineSection[]): OutlineSection[] {
  return sections.filter((section) => {
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
    children.forEach((section) => used.add(section.id));

    if (children.length === 1 && (groupId === "section-4" || groupId === "section-5")) {
      nodes.push({ kind: "leaf", section: children[0] });
      continue;
    }

    nodes.push({ kind: "group", id: groupId, label, sections: children });
  }

  for (const section of sections) {
    if (used.has(section.id) || isPlaceholder(section)) continue;
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
