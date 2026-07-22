"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

interface RunGoNoGoButtonProps {
  rfpId: string;
  hasPdf: boolean;
  hasDescription: boolean;
  onLoadingChange?: (loading: boolean) => void;
}

export function RunGoNoGoButton({
  rfpId,
  hasPdf,
  hasDescription,
  onLoadingChange,
}: RunGoNoGoButtonProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canAnalyze = hasPdf || hasDescription;

  function setAnalyzing(next: boolean) {
    setLoading(next);
    onLoadingChange?.(next);
  }

  async function handleAnalyze() {
    setAnalyzing(true);
    setError(null);
    try {
      const res = await fetch(`/api/rfps/${rfpId}/analyze`, { method: "POST" });
      const data = (await res.json()) as { detail?: string; error?: string };
      if (!res.ok) {
        setError(data.detail ?? data.error ?? "Analysis failed");
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the analysis service.");
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={handleAnalyze}
        disabled={loading || !canAnalyze}
        className="zo-btn secondary disabled:opacity-60"
        title={
          canAnalyze
            ? "Run AI Go/No-Go analysis against the knowledge base"
            : "Upload an RFP PDF or add a description first"
        }
      >
        {loading ? "Analyzing…" : "Run Go/No-Go Analysis"}
      </button>
      {!canAnalyze && (
        <p className="text-xs text-zo-text-muted">
          Add a PDF or description to run analysis.
        </p>
      )}
      {error && <p className="text-xs text-zo-error">{error}</p>}
    </div>
  );
}
