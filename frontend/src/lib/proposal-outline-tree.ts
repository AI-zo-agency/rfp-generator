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

function isPlaceholder(section: OutlineSection): boolean {
  return PLACEHOLDER_IDS.has(section.id);
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
  const complete = GROUP_ORDER.filter(({ id: groupId }) =>
    sections.some(
      (section) =>
        !isPlaceholder(section) &&
        matchesGroup(section, groupId) &&
        section.content.trim().length > 0,
    ),
  ).length;

  return {
    complete,
    total: GROUP_ORDER.length,
  };
}
