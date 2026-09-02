"use client";

import { Delete20Regular } from "@fluentui/react-icons";
import { useState } from "react";
import { createPortal } from "react-dom";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";

interface DeleteKnowledgeDocButtonProps {
  kbDocument: KnowledgeBaseDocument;
  onDeleted: () => void | Promise<void>;
}

export function DeleteKnowledgeDocButton({
  kbDocument,
  onDeleted,
}: DeleteKnowledgeDocButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function handleDelete() {
    setLoading(true);
    setError(null);
    const customId = kbDocument.supermemoryCustomId ?? "";
    const query = customId ? `?customId=${encodeURIComponent(customId)}` : "";
    try {
      const response = await fetch(
        `/api/knowledge-base/documents/${encodeURIComponent(kbDocument.id)}${query}`,
        { method: "DELETE" }
      );
      const data = (await response.json()) as { detail?: string; error?: string };
      if (!response.ok) {
        setError(
          data.detail ?? data.error ?? "Could not delete the document."
        );
        return;
      }
      setConfirmOpen(false);
      await onDeleted();
    } catch {
      setError("Could not delete the document.");
    } finally {
      setLoading(false);
    }
  }

  const modal =
    confirmOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-kb-doc-title"
          >
            <button
              type="button"
              className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]"
              aria-label="Close delete confirmation"
              onClick={() => !loading && setConfirmOpen(false)}
            />
            <div className="relative z-10 w-full max-w-md rounded-2xl border border-zo-border bg-white p-6 shadow-[0_24px_64px_rgba(15,23,42,0.12)]">
              <h2
                id="delete-kb-doc-title"
                className="font-heading text-lg font-bold text-foreground"
              >
                Delete this document?
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                <span className="font-medium text-foreground">{kbDocument.title}</span>{" "}
                will be permanently removed from Supermemory. Agents will no longer
                retrieve it on future runs. This cannot be undone.
              </p>
              {error ? (
                <p className="mt-3 text-sm text-zo-danger" role="alert">
                  {error}
                </p>
              ) : null}
              <div className="mt-6 flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmOpen(false)}
                  disabled={loading}
                  className="zo-btn secondary !py-2.5 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={loading}
                  className="zo-btn !border-zo-danger !bg-zo-danger !py-2.5 hover:!bg-red-700 disabled:opacity-50"
                >
                  {loading ? "Deleting…" : "Delete document"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setError(null);
          setConfirmOpen(true);
        }}
        disabled={loading}
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-red-600 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
        title={loading ? "Deleting…" : `Delete ${kbDocument.title}`}
        aria-label={loading ? "Deleting document" : `Delete ${kbDocument.title}`}
      >
        <Delete20Regular
          className={loading ? "h-5 w-5 animate-pulse" : "h-5 w-5"}
          aria-hidden
        />
      </button>
      {modal}
    </>
  );
}
