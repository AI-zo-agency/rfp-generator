"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

interface RunGoNoGoButtonProps {
  rfpId: string;
  hasPdf: boolean;
  hasDescription: boolean;
  onLoadingChange?: (loading: boolean) => void;
}

type AnalyzeStatus = {
  status?: string;
  error?: string;
  detail?: string;
};

const POLL_MS = 2500;
const MAX_WAIT_MS = 15 * 60 * 1000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

  async function pollUntilDone(): Promise<AnalyzeStatus> {
    const started = Date.now();
    while (Date.now() - started < MAX_WAIT_MS) {
      await sleep(POLL_MS);
      const res = await fetch(`/api/rfps/${rfpId}/analyze/status`, {
        cache: "no-store",
      });
      const data = (await res.json()) as AnalyzeStatus;
      if (!res.ok) {
        throw new Error(data.detail ?? data.error ?? "Status check failed");
      }
      if (data.status === "completed") return data;
      if (data.status === "failed") {
        throw new Error(data.error ?? data.detail ?? "Analysis failed");
      }
      // running | idle (briefly after start) — keep polling
    }
    throw new Error("Go/No-Go timed out after 15 minutes");
  }

  async function handleAnalyze() {
    setAnalyzing(true);
    setError(null);
    try {
      const res = await fetch(`/api/rfps/${rfpId}/analyze`, { method: "POST" });
      const data = (await res.json()) as AnalyzeStatus & {
        message?: string;
      };
      if (!res.ok) {
        setError(data.detail ?? data.error ?? "Analysis failed");
        return;
      }

      await pollUntilDone();
      router.refresh();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Could not reach the analysis service.";
      setError(message);
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
        {loading ? "Analyzing… (this can take a few minutes)" : "Run Go/No-Go Analysis"}
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
