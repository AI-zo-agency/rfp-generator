export interface KnowledgeBaseDocument {
  id: string;
  title: string;
  category: string;
  categoryTitle: string;
  fileName: string;
  mimeType: string;
  fileSize: number;
  uploadedAt: string;
  supermemoryCustomId?: string | null;
  supermemorySyncedAt?: string | null;
  supermemoryError?: string | null;
  supermemoryStatus?: string | null;
  supermemoryUrl?: string | null;
}
