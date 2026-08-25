"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

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
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Read inside the async poll loop to break it immediately when Stop is clicked
  // (a ref avoids the stale-closure a state value would have inside the loop).
  const stopRef = useRef(false);

  const canAnalyze = hasPdf || hasDescription;

  function setAnalyzing(next: boolean) {
    setLoading(next);
    onLoadingChange?.(next);
  }

  async function checkStatusOnce(): Promise<AnalyzeStatus> {
    const res = await fetch(`/api/rfps/${rfpId}/analyze/status`, {
      cache: "no-store",
    });
    const data = (await res.json()) as AnalyzeStatus;
    if (!res.ok) {
      throw new Error(data.detail ?? data.error ?? "Status check failed");
    }
    return data;
  }

  async function pollUntilDone(): Promise<AnalyzeStatus> {
    const started = Date.now();
    while (Date.now() - started < MAX_WAIT_MS) {
      if (stopRef.current) return { status: "idle" };
      await sleep(POLL_MS);
      if (stopRef.current) return { status: "idle" };
      const data = await checkStatusOnce();
      if (data.status === "completed") return data;
      if (data.status === "failed") {
        throw new Error(data.error ?? data.detail ?? "Analysis failed");
      }
      // running | idle (briefly after start) — keep polling
    }
    throw new Error("Go/No-Go timed out after 15 minutes");
  }

  async function handleStop() {
    stopRef.current = true;
    setStopping(true);
    try {
      await fetch(`/api/rfps/${rfpId}/analyze/stop`, { method: "POST" });
    } catch {
      // Still unstick the UI even if the stop request fails.
    }
    setAnalyzing(false);
    setStopping(false);
    setError(null);
    router.refresh();
  }

  // Reconnect on mount — an analysis started before a refresh (or from
  // another tab/session) is still tracked server-side (proposal_job_runner
  // — Redis-backed in production), so pick its progress back up instead of
  // showing "Run Go/No-Go Analysis" as if nothing were happening.
  useEffect(() => {
    let cancelled = false;

    async function reconnect() {
      try {
        const data = await checkStatusOnce();
        if (cancelled || data.status !== "running") return;
        setAnalyzing(true);
        setError(null);
        await pollUntilDone();
        if (!cancelled) router.refresh();
      } catch (err) {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Could not reach the analysis service.";
        setError(message);
      } finally {
        if (!cancelled) setAnalyzing(false);
      }
    }

    void reconnect();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfpId]);

  async function handleAnalyze() {
    stopRef.current = false;
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
      <div className="flex flex-wrap items-center gap-2">
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
        {loading && (
          <button
            type="button"
            onClick={handleStop}
            disabled={stopping}
            className="zo-btn disabled:opacity-60"
            style={{
              backgroundColor: "var(--zo-error)",
              borderColor: "var(--zo-error)",
              color: "#fff",
            }}
            title="Stop the running Go/No-Go analysis"
          >
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
      </div>
      {!canAnalyze && (
        <p className="text-xs text-zo-text-muted">
          Add a PDF or description to run analysis.
        </p>
      )}
      {error && <p className="text-xs text-zo-error">{error}</p>}
    </div>
  );
}
