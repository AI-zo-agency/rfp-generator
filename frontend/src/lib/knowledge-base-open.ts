import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

/** Open link — uses Supermemory url when listed, otherwise resolves on click. */
export function knowledgeBaseDocumentOpenHref(
  document: KnowledgeBaseDocument
): string {
  if (document.supermemoryUrl) {
    return document.supermemoryUrl;
  }
  const query = document.supermemoryCustomId
    ? `?customId=${encodeURIComponent(document.supermemoryCustomId)}`
    : "";
  return `/api/knowledge-base/documents/${encodeURIComponent(document.id)}/open${query}`;
}
