import { DeleteKnowledgeDocButton } from "@/components/DeleteKnowledgeDocButton";
import { UploadKnowledgeDocButton } from "@/components/UploadKnowledgeDocButton";
import { formatDate } from "@/lib/format";
import { kbAccentBgSoft, kbAccentText } from "@/lib/kb-brand";
import { knowledgeBaseDocumentOpenHref } from "@/lib/knowledge-base-open";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

function DocIcon({ className = `h-6 w-6 ${kbAccentText}` }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.5}
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
      />
    </svg>
  );
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DocumentViewProps {
  document: KnowledgeBaseDocument;
  containerTag: string;
  onDeleted: () => void | Promise<void>;
  showCategory?: boolean;
}

export function KnowledgeBaseDocumentCard({
  document,
  containerTag,
  onDeleted,
  showCategory = true,
}: DocumentViewProps) {
  return (
    <article className="zo-card group relative p-6 transition-shadow duration-200 hover:shadow-md">
      <div className="absolute right-4 top-4">
        <DeleteKnowledgeDocButton kbDocument={document} onDeleted={onDeleted} />
      </div>
      <div className="flex items-start gap-4 pr-10">
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${kbAccentBgSoft}`}>
          <DocIcon />
        </div>
        <div className="min-w-0 flex-1">
          {showCategory ? (
            <p className={`text-[10px] font-bold uppercase tracking-widest ${kbAccentText}`}>
              {document.categoryTitle}
            </p>
          ) : null}
          <h3 className="font-heading mt-1 truncate text-lg font-bold text-foreground group-hover:text-[#ef5018]">
            {document.title}
          </h3>
          <p className="mt-1 truncate text-xs text-zo-text-muted">
            {document.fileName}
            {document.fileSize > 0 ? ` · ${formatFileSize(document.fileSize)}` : ""}
          </p>
          {document.uploadedAt ? (
            <p className="mt-1 text-xs text-zo-text-muted">
              Indexed {formatDate(document.uploadedAt.split("T")[0])}
              {document.supermemoryStatus
                ? ` · ${document.supermemoryStatus}`
                : ""}
            </p>
          ) : null}
          <span className="mt-2 inline-flex rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
            In Supermemory · {containerTag}
          </span>
          {document.supermemoryError ? (
            <span className="mt-2 inline-flex rounded-full bg-red-50 px-2.5 py-0.5 text-[10px] font-semibold text-red-700 ring-1 ring-red-200">
              {document.supermemoryError}
            </span>
          ) : null}
        </div>
      </div>
      <div className="mt-5">
        <a
          href={knowledgeBaseDocumentOpenHref(document)}
          target="_blank"
          rel="noopener noreferrer"
          className={`inline-flex text-xs font-semibold ${kbAccentText} hover:text-[#d44312]`}
        >
          Open document →
        </a>
      </div>
    </article>
  );
}

export function KnowledgeBaseDocumentListRow({
  document,
  containerTag,
  onDeleted,
}: DocumentViewProps) {
  return (
    <article className="group flex items-center gap-4 border-b border-zo-border/70 px-4 py-3.5 last:border-b-0 hover:bg-zo-warm-gray/30">
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${kbAccentBgSoft}`}>
        <DocIcon className={`h-5 w-5 ${kbAccentText}`} />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="truncate font-semibold text-foreground group-hover:text-[#ef5018]">
          {document.title}
        </h3>
        <p className="mt-0.5 truncate text-xs text-zo-text-muted">
          {document.fileName}
          {document.fileSize > 0 ? ` · ${formatFileSize(document.fileSize)}` : ""}
          {document.uploadedAt
            ? ` · Indexed ${formatDate(document.uploadedAt.split("T")[0])}`
            : ""}
        </p>
      </div>
      <div className="hidden shrink-0 items-center gap-2 sm:flex">
        <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
          {containerTag}
        </span>
        {document.supermemoryError ? (
          <span className="rounded-full bg-red-50 px-2.5 py-0.5 text-[10px] font-semibold text-red-700 ring-1 ring-red-200">
            {document.supermemoryError}
          </span>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <a
          href={knowledgeBaseDocumentOpenHref(document)}
          target="_blank"
          rel="noopener noreferrer"
          className={`text-xs font-semibold ${kbAccentText} hover:text-[#d44312]`}
        >
          Open
        </a>
        <DeleteKnowledgeDocButton kbDocument={document} onDeleted={onDeleted} />
      </div>
    </article>
  );
}

export function KnowledgeBaseEmptyState({
  onUploaded,
  filtered,
}: {
  onUploaded: () => void | Promise<void>;
  filtered: boolean;
}) {
  return (
    <div className="zo-card flex flex-col items-center px-8 py-14 text-center">
      <DocIcon />
      <p className="font-heading mt-4 text-lg font-bold text-foreground">
        {filtered ? "No documents in this category" : "No documents yet"}
      </p>
      <p className="mt-2 max-w-md text-sm text-zo-text-muted">
        {filtered
          ? "Try another category filter or upload a document with this type."
          : "Upload verified facts, case studies, bios, or won proposals to build your knowledge base."}
      </p>
      {!filtered ? (
        <div className="mt-6">
          <UploadKnowledgeDocButton onUploaded={() => void onUploaded()} />
        </div>
      ) : null}
    </div>
  );
}
