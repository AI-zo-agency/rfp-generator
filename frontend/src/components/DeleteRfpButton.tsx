"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createPortal } from "react-dom";
import { IconTrash } from "./ui/icons";

interface DeleteRfpButtonProps {
  rfpId: string;
  title: string;
  redirectTo?: string;
  variant?: "detail" | "table";
}

export function DeleteRfpButton({
  rfpId,
  title,
  redirectTo = "/rfps",
  variant = "detail",
}: DeleteRfpButtonProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function handleDelete() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/rfps/${rfpId}`, { method: "DELETE" });
      const data = (await response.json()) as { detail?: string };
      if (!response.ok) {
        setError(data.detail ?? "Failed to delete RFP.");
        return;
      }
      setConfirmOpen(false);
      router.push(redirectTo);
      router.refresh();
    } catch {
      setError("Failed to delete RFP.");
    } finally {
      setLoading(false);
    }
  }

  const trigger =
    variant === "table" ? (
      <button
        type="button"
        onClick={() => setConfirmOpen(true)}
        disabled={loading}
        className="table-action-btn table-action-btn--danger"
        title={loading ? "Deleting…" : "Delete RFP"}
        aria-label={loading ? "Deleting RFP" : `Delete ${title}`}
      >
        <IconTrash className={loading ? "h-4 w-4 animate-pulse" : "h-4 w-4"} />
      </button>
    ) : (
      <button
        type="button"
        onClick={() => setConfirmOpen(true)}
        disabled={loading}
        className="zo-btn secondary inline-flex items-center gap-2 border-zo-danger/30 text-zo-danger hover:border-zo-danger hover:bg-zo-danger/8 disabled:opacity-60"
      >
        <IconTrash className="h-4 w-4" />
        {loading ? "Deleting…" : "Delete RFP"}
      </button>
    );

  const modal =
    confirmOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-rfp-title"
          >
            <button
              type="button"
              className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]"
              aria-label="Close delete confirmation"
              onClick={() => !loading && setConfirmOpen(false)}
            />
            <div className="relative z-10 w-full max-w-md rounded-2xl border border-zo-border bg-white p-6 shadow-[0_24px_64px_rgba(15,23,42,0.12)]">
              <h2
                id="delete-rfp-title"
                className="font-heading text-lg font-bold text-foreground"
              >
                Delete this RFP?
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                <span className="font-medium text-foreground">{title}</span> will
                be removed along with the saved proposal draft and uploaded PDF.
                This cannot be undone.
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
                  {loading ? "Deleting…" : "Delete RFP"}
                </button>
              </div>
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <>
      <div className="inline-flex flex-col items-center">
        {trigger}
        {error && !confirmOpen ? (
          <p
            className={`mt-1 text-zo-danger ${variant === "table" ? "max-w-[8rem] text-center text-[10px]" : "max-w-xs text-xs"}`}
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </div>
      {modal}
    </>
  );
}
