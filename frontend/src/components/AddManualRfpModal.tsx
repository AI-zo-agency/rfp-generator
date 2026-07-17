"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface AddManualRfpModalProps {
  open: boolean;
  onClose: () => void;
}

const defaultDueDate = (): string => {
  const date = new Date();
  date.setDate(date.getDate() + 30);
  return date.toISOString().slice(0, 10);
};

const fieldClass =
  "zo-input mt-1.5 w-full px-3 py-2.5 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10";

export function AddManualRfpModal({ open, onClose }: AddManualRfpModalProps) {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dueDate, setDueDate] = useState(defaultDueDate);
  const [dueDateHint, setDueDateHint] = useState<string | null>(null);
  const [extractingDate, setExtractingDate] = useState(false);

  const resetForm = useCallback(() => {
    setError(null);
    setSubmitting(false);
    setDueDate(defaultDueDate());
    setDueDateHint(null);
    setExtractingDate(false);
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

  async function handlePdfChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      setDueDateHint(null);
      return;
    }

    setExtractingDate(true);
    setDueDateHint(null);

    const formData = new FormData();
    formData.append("pdf", file);

    try {
      const response = await fetch("/api/rfps/extract-due-date", {
        method: "POST",
        body: formData,
      });
      const data = (await response.json()) as {
        dueDate?: string | null;
        error?: string;
      };

      if (response.ok && data.dueDate) {
        setDueDate(data.dueDate);
        setDueDateHint("Due date detected from PDF");
      } else {
        setDueDateHint("No due date found in PDF — enter manually");
      }
    } catch {
      setDueDateHint("Could not read PDF — enter due date manually");
    } finally {
      setExtractingDate(false);
    }
  }

  if (!open || !mounted) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const form = event.currentTarget;
    const formData = new FormData(form);
    formData.set("dueDate", dueDate);

    try {
      const response = await fetch("/api/rfps", {
        method: "POST",
        body: formData,
      });
      const data = (await response.json()) as {
        error?: string;
        rfp?: { id: string };
      };

      if (!response.ok) {
        setError(data.error ?? "Failed to add RFP.");
        setSubmitting(false);
        return;
      }

      onClose();
      form.reset();
      resetForm();
      router.refresh();
      if (data.rfp?.id) {
        router.push(`/rfps/${data.rfp.id}`);
      }
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
      aria-labelledby="add-manual-rfp-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Close dialog"
        onClick={onClose}
      />

      <div className="zo-card relative z-10 flex max-h-[min(90dvh,880px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-zo-border bg-[var(--zo-card-bg)] shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-zo-border px-6 py-5 md:px-8 md:py-6">
          <div className="min-w-0 pr-2">
            <p className="text-[11px] uppercase tracking-[0.28em] text-zo-orange">
              Manual intake
            </p>
            <h2
              id="add-manual-rfp-title"
              className="font-heading mt-2 text-2xl font-semibold text-foreground"
            >
              Add New RFP
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
              Enter opportunity details and attach the solicitation PDF. Due date
              is auto-detected from the PDF when possible.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg border border-zo-border px-3 py-1.5 text-sm text-zo-text-muted transition-smooth hover:border-zo-orange hover:text-foreground"
          >
            Close
          </button>
        </div>

        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={handleSubmit}
        >
          <div className="flex-1 overflow-y-auto px-6 py-5 md:px-8 md:py-6">
            <div className="grid grid-cols-1 gap-x-6 gap-y-5 sm:grid-cols-2">
              <label className="block sm:col-span-2">
                <span className="text-sm font-medium text-foreground">
                  Title <span className="text-zo-orange">*</span>
                </span>
                <input
                  name="title"
                  required
                  minLength={3}
                  placeholder="RFP title"
                  className={fieldClass}
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium text-foreground">
                  Client / agency <span className="text-zo-orange">*</span>
                </span>
                <input
                  name="client"
                  required
                  placeholder="Issuing organization"
                  className={fieldClass}
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium text-foreground">
                  Due date <span className="text-zo-orange">*</span>
                </span>
                <input
                  type="date"
                  name="dueDate"
                  required
                  value={dueDate}
                  onChange={(event) => {
                    setDueDate(event.target.value);
                    setDueDateHint(null);
                  }}
                  className={fieldClass}
                />
                {(extractingDate || dueDateHint) && (
                  <p className="mt-1.5 text-xs text-zo-text-muted">
                    {extractingDate
                      ? "Reading due date from PDF…"
                      : dueDateHint}
                  </p>
                )}
              </label>

              <label className="block">
                <span className="text-sm font-medium text-foreground">
                  Location
                </span>
                <input
                  name="location"
                  placeholder="City, state or remote"
                  className={fieldClass}
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium text-foreground">
                  Sector
                </span>
                <input
                  name="sector"
                  defaultValue="Public Sector"
                  className={fieldClass}
                />
              </label>

              <label className="block sm:col-span-2">
                <span className="text-sm font-medium text-foreground">
                  Solicitation PDF
                </span>
                <input
                  type="file"
                  name="pdf"
                  accept="application/pdf,.pdf"
                  onChange={handlePdfChange}
                  className={`${fieldClass} file:mr-3 file:rounded-md file:border-0 file:bg-zo-orange file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white`}
                />
              </label>
            </div>

            {error && (
              <p className="mt-5 rounded-xl border border-zo-error/30 bg-zo-error/10 px-4 py-3 text-sm text-zo-error">
                {error}
              </p>
            )}
          </div>

          <div className="flex shrink-0 flex-col-reverse gap-3 border-t border-zo-border px-6 py-4 sm:flex-row sm:justify-end md:px-8 md:py-5">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="zo-btn secondary w-full sm:w-auto"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || extractingDate}
              className="zo-btn w-full sm:w-auto disabled:opacity-60"
            >
              {submitting ? "Saving…" : "Add RFP"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
