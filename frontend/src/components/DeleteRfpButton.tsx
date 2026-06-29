"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
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

  async function handleDelete() {
    const confirmed = globalThis.confirm(
      `Delete "${title}"?\n\nThis removes the RFP record, saved proposal draft, and uploaded PDF. This cannot be undone.`,
    );
    if (!confirmed) return;

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/rfps/${rfpId}`, { method: "DELETE" });
      const data = (await response.json()) as { detail?: string };
      if (!response.ok) {
        setError(data.detail ?? "Failed to delete RFP.");
        return;
      }
      router.push(redirectTo);
      router.refresh();
    } catch {
      setError("Failed to delete RFP.");
    } finally {
      setLoading(false);
    }
  }

  if (variant === "table") {
    return (
      <div className="inline-flex flex-col items-center">
        <button
          type="button"
          onClick={handleDelete}
          disabled={loading}
          className="table-action-btn table-action-btn--danger"
          title={loading ? "Deleting…" : "Delete RFP"}
          aria-label={loading ? "Deleting RFP" : `Delete ${title}`}
        >
          <IconTrash className={loading ? "h-4 w-4 animate-pulse" : "h-4 w-4"} />
        </button>
        {error ? (
          <p className="mt-1 max-w-[8rem] text-center text-[10px] text-zo-danger" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="inline-flex flex-col gap-2">
      <button
        type="button"
        onClick={handleDelete}
        disabled={loading}
        className="zo-btn secondary inline-flex items-center gap-2 border-zo-danger/30 text-zo-danger hover:border-zo-danger hover:bg-zo-danger/8 disabled:opacity-60"
      >
        <IconTrash className="h-4 w-4" />
        {loading ? "Deleting…" : "Delete RFP"}
      </button>
      {error ? (
        <p className="max-w-xs text-xs text-zo-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
