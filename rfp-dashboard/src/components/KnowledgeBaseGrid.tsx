"use client";

import { useCallback, useEffect, useState } from "react";
import { formatDate } from "@/lib/format";
import type { KnowledgeBaseStatus } from "@/lib/knowledge-base-api";
import { UploadKnowledgeDocButton } from "@/components/UploadKnowledgeDocButton";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

function DocIcon() {
  return (
    <svg
      className="h-6 w-6 text-zo-teal"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
      />
    </svg>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function KnowledgeBaseGrid() {
  const [documents, setDocuments] = useState<KnowledgeBaseDocument[]>([]);
  const [status, setStatus] = useState<KnowledgeBaseStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const [docsRes, statusRes] = await Promise.all([
        fetch("/api/knowledge-base/documents"),
        fetch("/api/knowledge-base/status"),
      ]);

      if (docsRes.ok) {
        const body = (await docsRes.json()) as {
          documents: KnowledgeBaseDocument[];
        };
        setDocuments(body.documents ?? []);
      } else {
        setDocuments([]);
      }

      if (statusRes.ok) {
        setStatus((await statusRes.json()) as KnowledgeBaseStatus);
      }
    } catch {
      setDocuments([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  const categoryCount = new Set(documents.map((doc) => doc.category)).size;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <UploadKnowledgeDocButton onUploaded={() => void loadDocuments()} />
        <div className="flex flex-wrap gap-2">
          {status?.supermemoryConfigured ? (
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
              Supermemory · {status.containerTag}
            </span>
          ) : (
            <span className="rounded-full bg-red-50 px-3 py-1 text-xs font-semibold text-red-700 ring-1 ring-red-200">
              Supermemory not configured
            </span>
          )}
          <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800 ring-1 ring-amber-200">
            Drive not connected
          </span>
        </div>
      </div>

      <div className="grid gap-5 sm:grid-cols-3">
        <div className="zo-card p-6">
          <p className="text-[11px] font-bold uppercase tracking-widest text-zo-text-muted">
            Documents
          </p>
          <p className="font-heading mt-3 text-4xl font-bold text-zo-orange">
            {loading ? "—" : documents.length}
          </p>
        </div>
        <div className="zo-card p-6">
          <p className="text-[11px] font-bold uppercase tracking-widest text-zo-text-muted">
            Categories
          </p>
          <p className="font-heading mt-3 text-4xl font-bold text-foreground">
            {loading ? "—" : categoryCount}
          </p>
        </div>
        <div className="zo-card p-6">
          <p className="text-[11px] font-bold uppercase tracking-widest text-zo-text-muted">
            Source
          </p>
          <p className="mt-3 text-sm font-semibold text-foreground">
            Supermemory only
          </p>
        </div>
      </div>

      <div>
        <div className="mb-5">
          <h2 className="font-heading text-xl font-bold text-foreground">
            Uploaded documents
          </h2>
            <p className="mt-1 text-sm text-zo-text-muted">
              Documents live in Supermemory container{" "}
              <strong className="font-semibold text-foreground">
                {status?.containerTag ?? "zo-agency"}
              </strong>
              — nothing is saved on this machine.
            </p>
        </div>

        {loading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {["a", "b", "c", "d", "e", "f"].map((key) => (
              <div
                key={key}
                className="zo-card h-36 animate-pulse bg-zo-warm-gray/40"
              />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <div className="zo-card flex flex-col items-center px-8 py-14 text-center">
            <DocIcon />
            <p className="font-heading mt-4 text-lg font-bold text-foreground">
              No documents yet
            </p>
            <p className="mt-2 max-w-md text-sm text-zo-text-muted">
              Upload verified facts, case studies, bios, or won proposals to
              build your knowledge base.
            </p>
            <div className="mt-6">
              <UploadKnowledgeDocButton onUploaded={() => void loadDocuments()} />
            </div>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {documents.map((document) => (
              <article
                key={document.id}
                className="zo-card group p-6 transition-shadow duration-200 hover:shadow-md"
              >
                <div className="flex items-start gap-4">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-zo-teal/10">
                    <DocIcon />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-zo-teal">
                      {document.categoryTitle}
                    </p>
                    <h3 className="font-heading mt-1 truncate text-lg font-bold text-foreground group-hover:text-zo-orange">
                      {document.title}
                    </h3>
                    <p className="mt-1 truncate text-xs text-zo-text-muted">
                      {document.fileName}
                      {document.fileSize > 0
                        ? ` · ${formatFileSize(document.fileSize)}`
                        : ""}
                    </p>
                    {document.uploadedAt && (
                      <p className="mt-1 text-xs text-zo-text-muted">
                        Indexed{" "}
                        {formatDate(document.uploadedAt.split("T")[0])}
                        {document.supermemoryStatus
                          ? ` · ${document.supermemoryStatus}`
                          : ""}
                      </p>
                    )}
                    <span className="mt-2 inline-flex rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
                      In Supermemory · {status?.containerTag ?? "zo-agency"}
                    </span>
                    {document.supermemoryError && (
                      <span className="mt-2 inline-flex rounded-full bg-red-50 px-2.5 py-0.5 text-[10px] font-semibold text-red-700 ring-1 ring-red-200">
                        {document.supermemoryError}
                      </span>
                    )}
                  </div>
                </div>
                {document.supermemoryUrl ? (
                  <a
                    href={document.supermemoryUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-5 inline-flex text-xs font-semibold text-zo-teal hover:text-zo-orange"
                  >
                    Open in Supermemory →
                  </a>
                ) : (
                  <p className="mt-5 text-xs text-zo-text-muted">
                    Stored in Supermemory — open the zo-agency container in your
                    Supermemory dashboard to view.
                  </p>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
