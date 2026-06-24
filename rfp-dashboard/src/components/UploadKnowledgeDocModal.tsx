"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  KB_DOCUMENT_TYPES,
  SUPERMEMORY_CONTAINER_TAG,
} from "@/lib/kb-document-types";

interface UploadKnowledgeDocModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

const fieldClass =
  "zo-input mt-1.5 w-full px-3 py-2.5 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10";

export function UploadKnowledgeDocModal({
  open,
  onClose,
  onSuccess,
}: UploadKnowledgeDocModalProps) {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetForm = useCallback(() => {
    setError(null);
    setSubmitting(false);
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    resetForm();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    globalThis.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      globalThis.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose, resetForm]);

  if (!open || !mounted) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const form = event.currentTarget;
    const formData = new FormData(form);

    try {
      const response = await fetch("/api/knowledge-base/documents", {
        method: "POST",
        body: formData,
      });
      const data = (await response.json()) as {
        error?: string;
        detail?: string;
      };

      if (!response.ok) {
        setError(data.detail ?? data.error ?? "Upload failed.");
        setSubmitting(false);
        return;
      }

      onClose();
      form.reset();
      onSuccess?.();
      router.refresh();
    } catch {
      setError("Network error. Please try again.");
      setSubmitting(false);
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="upload-kb-doc-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Close dialog"
        onClick={onClose}
      />

      <div className="zo-card relative z-10 flex max-h-[min(90dvh,720px)] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-zo-border bg-[var(--zo-card-bg)] shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-zo-border px-6 py-5 md:px-8">
          <div className="min-w-0 pr-2">
            <p className="text-[11px] uppercase tracking-[0.28em] text-zo-orange">
              Manual upload
            </p>
            <h2
              id="upload-kb-doc-title"
              className="font-heading mt-2 text-2xl font-semibold text-foreground"
            >
              Add to Knowledge Base
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
              Files go straight to Supermemory container{" "}
              <strong className="font-semibold text-foreground">
                {SUPERMEMORY_CONTAINER_TAG}
              </strong>
              . Nothing is stored on localhost.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shell-icon-btn flex h-9 w-9 shrink-0 items-center justify-center"
            aria-label="Close"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form
          onSubmit={(event) => void handleSubmit(event)}
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5 md:px-8">
            <label className="block text-sm font-medium text-foreground">
              Title
              <input
                name="title"
                required
                placeholder="e.g. City of San Leandro — won proposal"
                className={fieldClass}
              />
            </label>

            <label className="block text-sm font-medium text-foreground">
              Document type
              <select name="category" required defaultValue="" className={fieldClass}>
                <option value="" disabled>
                  Select type…
                </option>
                {KB_DOCUMENT_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label}
                  </option>
                ))}
              </select>
              <span className="mt-1.5 block text-xs text-zo-text-muted">
                Stored in Supermemory as{" "}
                <code className="text-[11px]">{SUPERMEMORY_CONTAINER_TAG}</code>
              </span>
            </label>

            <label className="block text-sm font-medium text-foreground">
              Document
              <input
                name="file"
                type="file"
                required
                accept=".pdf,.doc,.docx,.md,.txt,.xls,.xlsx"
                className="mt-1.5 block w-full text-sm text-zo-text-secondary file:mr-4 file:rounded-lg file:border-0 file:bg-zo-orange/10 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-zo-orange hover:file:bg-zo-orange/15"
              />
              <span className="mt-1.5 block text-xs text-zo-text-muted">
                PDF, Word, Excel, Markdown, or text — max 25 MB. Large PDFs may
                take up to a minute to index in Supermemory.
              </span>
            </label>

            {error && (
              <p className="rounded-xl border border-zo-error/30 bg-zo-error/10 px-4 py-3 text-sm text-zo-error">
                {error}
              </p>
            )}
          </div>

          <div className="flex shrink-0 justify-end gap-3 border-t border-zo-border px-6 py-4 md:px-8">
            <button
              type="button"
              onClick={onClose}
              className="zo-btn secondary !py-2.5"
              disabled={submitting}
            >
              Cancel
            </button>
            <button type="submit" className="zo-btn !py-2.5" disabled={submitting}>
              {submitting ? "Uploading & indexing…" : "Upload document"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
