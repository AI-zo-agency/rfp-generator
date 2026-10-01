import { KB_DOCUMENT_TYPES, resolveCategoryLabel } from "@/lib/kb-document-types";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

export type KbViewMode = "grid" | "list";

export const KB_VIEW_MODE_STORAGE_KEY = "zo-kb-documents-view";

export interface KbCategoryGroup {
  category: string;
  categoryTitle: string;
  documents: KnowledgeBaseDocument[];
}

const CATEGORY_ORDER = KB_DOCUMENT_TYPES.map((type) => type.value);

/**
 * The internal pricing doc has its own backend category (that is how agent search
 * hides it), but people think of it as Pricing, so the page shows one Pricing group.
 */
export function displayCategory(category: string): string {
  return category === "pricing_internal" ? "pricing" : category;
}

function categorySortIndex(category: string): number {
  const index = CATEGORY_ORDER.indexOf(category);
  return index === -1 ? CATEGORY_ORDER.length + 1 : index;
}

export function groupDocumentsByCategory(
  documents: KnowledgeBaseDocument[]
): KbCategoryGroup[] {
  const groups = new Map<string, KbCategoryGroup>();

  for (const document of documents) {
    const category = displayCategory(document.category);
    const existing = groups.get(category);
    if (existing) {
      existing.documents.push(document);
      continue;
    }
    groups.set(category, {
      category,
      // A merged group takes the canonical label, not the first document's own title.
      categoryTitle:
        (category === document.category ? document.categoryTitle : "") ||
        resolveCategoryLabel(category) ||
        category,
      documents: [document],
    });
  }

  return [...groups.values()]
    .map((group) => ({
      ...group,
      documents: [...group.documents].sort((left, right) =>
        right.uploadedAt.localeCompare(left.uploadedAt)
      ),
    }))
    .sort(
      (left, right) =>
        categorySortIndex(left.category) - categorySortIndex(right.category) ||
        left.categoryTitle.localeCompare(right.categoryTitle)
    );
}

export function categoryCounts(
  documents: KnowledgeBaseDocument[]
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const document of documents) {
    const category = displayCategory(document.category);
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  return counts;
}

export function filterDocumentsByCategory(
  documents: KnowledgeBaseDocument[],
  category: string | "all"
): KnowledgeBaseDocument[] {
  if (category === "all") return documents;
  return documents.filter((document) => displayCategory(document.category) === category);
}

export function readStoredViewMode(): KbViewMode {
  if (typeof window === "undefined") return "grid";
  const stored = window.localStorage.getItem(KB_VIEW_MODE_STORAGE_KEY);
  return stored === "list" ? "list" : "grid";
}

export function storeViewMode(mode: KbViewMode): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KB_VIEW_MODE_STORAGE_KEY, mode);
}
