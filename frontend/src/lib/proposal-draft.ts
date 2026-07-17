import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";

// Prefix-based section detection — subsections like section-1-who-we-are, section-2-bio-sonja, section-3-work-01-...
export const STATIC_SECTION_PREFIXES = [
  "section-1-",
  "section-2-bio-",
  "section-3-work-",
] as const;

// Legacy IDs kept for backwards compat with saved drafts
export const STATIC_SECTION_IDS = [
  "section-1-company-overview",
  "section-2-team-overview",
  "section-3-our-work",
] as const;

export const LEGACY_MONOLITH_SECTION_IDS = new Set([
  "section-1-company-overview",
  "section-2-team-overview",
  "section-3-our-work",
]);

/** Drop pre-subsection monoliths so wrong-client copy cannot reappear in the UI. */
export function stripLegacyMonolithSections(
  draft: ProposalOutline
): ProposalOutline {
  const sections = draft.sections.filter(
    (s) => !LEGACY_MONOLITH_SECTION_IDS.has(s.id)
  );
  if (sections.length === draft.sections.length) return draft;
  return { ...draft, sections };
}

export function staticSections1to3Complete(
  draft: ProposalOutline | null
): boolean {
  if (!draft) return false;
  // Modern subsections only — legacy monoliths (company-overview / team-overview / our-work)
  // must not count as complete or they keep resurfacing wrong-client copy after reset.
  const hasSection1 = draft.sections.some(
    (s) =>
      s.id.startsWith("section-1-") &&
      s.id !== "section-1-company-overview" &&
      s.content?.trim()
  );
  const hasSection2 = draft.sections.some(
    (s) =>
      s.id.startsWith("section-2-bio-") &&
      s.id !== "section-2-bio-placeholder" &&
      s.content?.trim()
  );
  const hasSection3 = draft.sections.some(
    (s) =>
      s.id.startsWith("section-3-work-") &&
      s.id !== "section-3-work-placeholder" &&
      s.content?.trim()
  );
  return hasSection1 && hasSection2 && hasSection3;
}

const DEFAULT_SECTIONS: (Omit<
  OutlineSection,
  "content" | "status"
>)[] = [
  // Section 1 — Company Overview subsections
  {
    id: "section-1-who-we-are",
    title: "1.1 — Who We Are",
    pageLimit: 1,
    wordTarget: 600,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
    {
    id: "section-1-org-structure",
    title: "1.2 — Organizational Structure",
    pageLimit: 2,
    wordTarget: 800,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
  {
    id: "section-1-business-info",
    title: "1.3 — Business Information",
    pageLimit: 1,
    wordTarget: 400,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
  {
    id: "section-1-certifications",
    title: "1.4 — Certifications",
    pageLimit: 1,
    wordTarget: 400,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
  {
    id: "section-1-insurance",
    title: "1.5 — Insurance Information",
    pageLimit: 1,
    wordTarget: 400,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
  // Section 2 — Team Bios (placeholder; subsections generated dynamically)
  {
    id: "section-2-bio-placeholder",
    title: "2.x — Team Bios (generated per member)",
    pageLimit: 2,
    wordTarget: 500,
    required: true,
    custom: false,
    source: "template",
    mode: "select",
  },
  // Section 3 — Our Work (placeholder; subsections generated dynamically)
  {
    id: "section-3-work-placeholder",
    title: "3.x — Our Work (generated per example)",
    pageLimit: 2,
    wordTarget: 600,
    required: true,
    custom: false,
    source: "template",
    mode: "select",
  },
  {
    id: "section-4-project-approach",
    title: "Section 4 — Project Approach",
    pageLimit: 8,
    wordTarget: 1800,
    required: true,
    custom: false,
    source: "generated",
    mode: "write",
  },
  {
    id: "section-5-scope-of-work",
    title: "Section 5 — Scope of Work",
    pageLimit: 6,
    wordTarget: 1500,
    required: true,
    custom: false,
    source: "generated",
    mode: "write",
  },
];

function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

export function buildDefaultOutline(rfp: RfpRecord): ProposalOutline {
  const pageBudget = rfp.pageLimit ?? 30;
  const scale = pageBudget / 34;

  const sections: OutlineSection[] = DEFAULT_SECTIONS.map((section) => ({
    ...section,
    pageLimit: section.pageLimit
      ? Math.max(1, Math.round(section.pageLimit * scale))
      : undefined,
    content: "",
    status: "outline" as const,
  }));

  return {
    sections,
    updatedAt: new Date().toISOString(),
  };
}

export function countSectionsWithContent(outline: ProposalOutline): number {
  return outline.sections.filter((s) => s.content?.trim()).length;
}

/** Empty 5-section shell saved over a researched manuscript (autosave wipe). */
export function isLikelyWipedOutline(
  outline: ProposalOutline,
  research: ProposalResearch | null
): boolean {
  if (countSectionsWithContent(outline) > 0) return false;
  const mappedSections = research?.rfpSections?.length ?? 0;
  const evidence = research?.evidenceCorpus?.length ?? 0;
  const hadManuscriptWork = mappedSections > 5 || evidence > 20;
  return hadManuscriptWork && outline.sections.length <= 5;
}

/** Restore section list from Phase 2 research when draft text was cleared. */
export function rebuildOutlineFromResearch(
  rfp: RfpRecord,
  research: ProposalResearch,
  existingDraft?: ProposalOutline | null
): ProposalOutline {
  const defaults = buildDefaultOutline(rfp);
  const existingById = new Map(
    (existingDraft?.sections ?? []).map((section) => [section.id, section])
  );

  const staticSections: OutlineSection[] = STATIC_SECTION_IDS.map((id) => {
    const fromDraft = existingById.get(id);
    const fromDefault = defaults.sections.find((s) => s.id === id);
    const base = fromDraft ?? fromDefault;
    if (!base) {
      throw new Error(`Missing static section ${id}`);
    }
    return { ...base, content: fromDraft?.content ?? "", status: fromDraft?.content ? "generated" : "outline" };
  });

  const staticIds = new Set(STATIC_SECTION_IDS);
  const rfpSections: OutlineSection[] = (research.rfpSections ?? [])
    .filter((mapped) => !staticIds.has(mapped.id as (typeof STATIC_SECTION_IDS)[number]))
    .map((mapped) => {
      const fromDraft = existingById.get(mapped.id);
      const content = fromDraft?.content ?? "";
      return {
        id: mapped.id,
        title: mapped.title,
        pageLimit: mapped.pageLimit ?? undefined,
        wordTarget: mapped.pageLimit
          ? Math.max(300, mapped.pageLimit * 350)
          : fromDraft?.wordTarget ?? 800,
        required: true,
        custom: false,
        content,
        status: content ? "generated" : "outline",
        source: "rfp" as const,
        mode: mapped.zoMode ?? "write",
      };
    });

  const preservedIds = new Set([
    ...staticSections.map((s) => s.id),
    ...rfpSections.map((s) => s.id),
  ]);
  const customSections = (existingDraft?.sections ?? []).filter(
    (section) => !preservedIds.has(section.id)
  );

  return {
    sections: [...staticSections, ...rfpSections, ...customSections],
    updatedAt: new Date().toISOString(),
  };
}

export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

export function estimatePages(wordCount: number): number {
  return Math.max(1, Math.ceil(wordCount / 300));
}

export function computeDraftStats(outline: ProposalOutline): {
  totalWords: number;
  totalPages: number;
  generatedSections: number;
} {
  const totalWords = outline.sections.reduce(
    (sum, s) => sum + countWords(s.content),
    0
  );
  const generatedSections = outline.sections.filter(
    (s) => s.status === "generated" || s.status === "reviewed"
  ).length;

  return {
    totalWords,
    totalPages: estimatePages(totalWords),
    generatedSections,
  };
}

export function createCustomSection(title: string): OutlineSection {
  return {
    id: `custom-${Date.now()}-${slugify(title)}`,
    title,
    wordTarget: 500,
    required: false,
    custom: true,
    source: "custom",
    content: "",
    status: "outline",
  };
}
