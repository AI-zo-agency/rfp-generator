"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

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
    const confirmed = window.confirm(
      `Delete "${title}"?\n\nThis removes the RFP record, saved proposal draft, and uploaded PDF. This cannot be undone.`
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

  const className =
    variant === "table"
      ? "text-xs font-semibold text-zo-error transition-colors hover:text-red-400 disabled:opacity-60"
      : "zo-btn secondary border-zo-error/40 text-zo-error hover:border-zo-error hover:bg-zo-error/10 disabled:opacity-60";

  return (
    <div className={variant === "table" ? "inline-flex flex-col items-end" : "inline-flex flex-col gap-2"}>
      <button
        type="button"
        onClick={handleDelete}
        disabled={loading}
        className={className}
      >
        {loading ? "Deleting…" : variant === "table" ? "Delete" : "Delete RFP"}
      </button>
      {error && (
        <p className="max-w-xs text-xs text-zo-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
