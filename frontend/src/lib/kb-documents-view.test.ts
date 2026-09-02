import { describe, expect, it } from "vitest";
import {
  categoryCounts,
  filterDocumentsByCategory,
  groupDocumentsByCategory,
} from "@/lib/kb-documents-view";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

function doc(
  overrides: Partial<KnowledgeBaseDocument> & Pick<KnowledgeBaseDocument, "id" | "category">
): KnowledgeBaseDocument {
  return {
    title: overrides.title ?? "Doc",
    categoryTitle: overrides.categoryTitle ?? overrides.category,
    fileName: "file.pdf",
    mimeType: "application/pdf",
    fileSize: 0,
    uploadedAt: overrides.uploadedAt ?? "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

describe("kb-documents-view", () => {
  it("groups documents by category in canonical order", () => {
    const documents = [
      doc({ id: "1", category: "reference", categoryTitle: "Reference / Guides" }),
      doc({ id: "2", category: "pricing", categoryTitle: "Pricing" }),
      doc({ id: "3", category: "pricing", categoryTitle: "Pricing" }),
    ];

    const groups = groupDocumentsByCategory(documents);
    expect(groups.map((group) => group.category)).toEqual(["pricing", "reference"]);
    expect(groups[0]?.documents).toHaveLength(2);
  });

  it("filters documents by selected category", () => {
    const documents = [
      doc({ id: "1", category: "pricing" }),
      doc({ id: "2", category: "reference" }),
    ];

    expect(filterDocumentsByCategory(documents, "pricing")).toHaveLength(1);
    expect(filterDocumentsByCategory(documents, "all")).toHaveLength(2);
  });

  it("counts documents per category", () => {
    const documents = [
      doc({ id: "1", category: "pricing" }),
      doc({ id: "2", category: "pricing" }),
      doc({ id: "3", category: "reference" }),
    ];

    expect(categoryCounts(documents).get("pricing")).toBe(2);
    expect(categoryCounts(documents).get("reference")).toBe(1);
  });
});
