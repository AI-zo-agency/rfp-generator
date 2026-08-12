"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { IconSync } from "./ui/icons";

interface SyncJustWinModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

type SyncMode = "today" | "specific" | "all";
type TabFilter = "all" | "hot" | "warm" | "review";

const getTodayIso = (): string => {
  // Local calendar date — matches how JustWin renders the "Posted" column in
  // the browser. toISOString() is UTC and skipped same-day leads for IST/US users.
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

const getPastDateIso = (daysAgo: number): string => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
};

export function SyncJustWinModal({
  open,
  onClose,
  onSuccess,
}: SyncJustWinModalProps) {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [syncMode, setSyncMode] = useState<SyncMode>("today");
  const [syncDate, setSyncDate] = useState<string>(getTodayIso());
  const [tabFilter, setTabFilter] = useState<TabFilter>("all");
  const [syncing, setSyncing] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successResult, setSuccessResult] = useState<{
    message: string;
    finishedAt?: string;
  } | null>(null);

  // The sync runs in the background on the server, so the POST only tells us it
  // started. Poll the job until it reports a terminal state before claiming
  // success and refreshing the dashboard.
  const waitForJob = useCallback(
    async (
      jobId: string
    ): Promise<{
      rfpsFound: number;
      rfpsCreated: number;
      rfpsSkipped: number;
      pdfsDownloaded: number;
    }> => {
      const deadline = Date.now() + 10 * 60 * 1000;

      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 2500));

        const res = await fetch("/api/justwin/status", { cache: "no-store" });
        if (!res.ok) continue;

        const job = (await res.json()) as {
          id?: string;
          status?: string;
          rfpsFound?: number;
          rfpsCreated?: number;
          rfpsSkipped?: number;
          pdfsDownloaded?: number;
          error?: string | null;
        };

        // Ignore a previous run's job that may still be the latest one.
        if (job.id && job.id !== jobId) continue;

        if (job.status === "completed") {
          const found = job.rfpsFound ?? 0;
          const skipped = job.rfpsSkipped ?? 0;
          const created =
            typeof job.rfpsCreated === "number"
              ? job.rfpsCreated
              : Math.max(0, found - skipped);
          return {
            rfpsFound: found,
            rfpsCreated: created,
            rfpsSkipped: skipped,
            pdfsDownloaded: job.pdfsDownloaded ?? 0,
          };
        }
        if (job.status === "failed") {
          throw new Error(job.error || "The JustWin sync failed. Check the backend logs.");
        }
      }

      throw new Error("Sync timed out after 10 minutes. Check the backend logs.");
    },
    []
  );

  const resetForm = useCallback(() => {
    setSyncMode("today");
    setSyncDate(getTodayIso());
    setTabFilter("all");
    setSyncing(false);
    setStatusMessage(null);
    setErrorMessage(null);
    setSuccessResult(null);
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Only reset form when modal opens
  useEffect(() => {
    if (open) {
      resetForm();
    }
  }, [open, resetForm]);

  // Handle overflow and escape key
  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !syncing) onClose();
    };

    globalThis.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      globalThis.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose, syncing]);

  if (!open || !mounted) return null;

  async function handleStartSync(e?: React.FormEvent) {
    if (e) e.preventDefault();
    setSyncing(true);
    setErrorMessage(null);
    setSuccessResult(null);
    setStatusMessage("Connecting to FastAPI backend & JustWin service...");

    const targetDate =
      syncMode === "today" ? getTodayIso() : syncMode === "all" ? "" : syncDate;

    const scope = targetDate ? `for ${targetDate}` : "across all dates";

    try {
      const response = await fetch("/api/justwin/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          syncMode,
          syncDate: targetDate,
          tab: tabFilter,
        }),
      });

      const result = (await response.json()) as {
        jobId?: string;
        error?: string;
      };

      if (!response.ok) {
        throw new Error(result.error || "Failed to start JustWin sync");
      }

      setStatusMessage(`Fetching ${tabFilter} leads ${scope}...`);

      const { rfpsFound, rfpsCreated, rfpsSkipped, pdfsDownloaded } = await waitForJob(
        result.jobId ?? ""
      );

      let message: string;
      if (rfpsFound === 0) {
        message = `No ${tabFilter} leads found ${scope}.`;
      } else if (rfpsCreated === 0 && rfpsSkipped > 0) {
        message =
          `No new RFPs ${scope} — ${rfpsSkipped} already in ZO were skipped` +
          (pdfsDownloaded > 0
            ? ` (${pdfsDownloaded} missing PDF${pdfsDownloaded === 1 ? "" : "s"} backfilled).`
            : ".");
      } else if (rfpsSkipped > 0) {
        message =
          `Added ${rfpsCreated} new RFP${rfpsCreated === 1 ? "" : "s"} ${scope}` +
          ` (${rfpsSkipped} already synced skipped` +
          `, ${pdfsDownloaded} PDF${pdfsDownloaded === 1 ? "" : "s"} downloaded).`;
      } else {
        message =
          `Synced ${rfpsCreated} ${tabFilter} RFP${rfpsCreated === 1 ? "" : "s"} ${scope}` +
          ` (${pdfsDownloaded} PDF${pdfsDownloaded === 1 ? "" : "s"} downloaded).`;
      }

      setSuccessResult({
        message,
        finishedAt: new Date().toISOString(),
      });

      router.refresh();
      if (onSuccess) onSuccess();
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "An unexpected error occurred during sync"
      );
    } finally {
      setSyncing(false);
      setStatusMessage(null);
    }
  }

  const modalContent = (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="sync-modal-title"
    >
      {/* Backdrop */}
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Close dialog"
        onClick={() => {
          if (!syncing) onClose();
        }}
      />

      {/* Modal Dialog Body */}
      <div className="zo-card relative z-10 flex w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-zo-border bg-[var(--zo-card-bg)] text-foreground shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-zo-border px-6 py-5 md:px-7 md:py-6">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zo-orange/10 text-zo-orange">
              <IconSync className={`h-5 w-5 ${syncing ? "animate-spin" : ""}`} />
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-zo-orange">
                JustWin Sync
              </p>
              <h2
                id="sync-modal-title"
                className="font-heading mt-0.5 text-xl font-semibold text-foreground"
              >
                Sync JustWin RFPs
              </h2>
              <p className="mt-1 text-xs text-zo-text-secondary">
                Re-syncing a date skips RFPs already in ZO — no duplicates.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={syncing}
            className="shrink-0 rounded-lg border border-zo-border px-3 py-1.5 text-sm text-zo-text-muted transition-smooth hover:border-zo-orange hover:text-foreground disabled:opacity-30"
          >
            Close
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleStartSync} className="flex flex-col gap-5 p-6 md:p-7">
          {/* Target Date Mode Selection */}
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-zo-text-muted">
              Select Sync Target Date
            </label>
            <div className="grid grid-cols-3 gap-3">
              {/* Option 1: Today */}
              <button
                type="button"
                onClick={() => {
                  setSyncMode("today");
                  setSyncDate(getTodayIso());
                }}
                disabled={syncing}
                className={`relative flex flex-col items-start rounded-xl border p-3 text-left transition-all ${
                  syncMode === "today"
                    ? "border-zo-orange bg-zo-orange/10 ring-2 ring-zo-orange/20"
                    : "border-zo-border bg-[var(--zo-bg-subtle,rgba(0,0,0,0.02))] hover:border-zo-orange/50"
                }`}
              >
                <div className="flex w-full items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">⚡ Today</span>
                  {syncMode === "today" && (
                    <span className="h-2.5 w-2.5 rounded-full bg-zo-orange" />
                  )}
                </div>
                <span className="mt-1 text-xs leading-relaxed text-zo-text-secondary">
                  Sync today ({getTodayIso()})
                </span>
              </button>

              {/* Option 2: Specific Date */}
              <button
                type="button"
                onClick={() => setSyncMode("specific")}
                disabled={syncing}
                className={`relative flex flex-col items-start rounded-xl border p-3 text-left transition-all ${
                  syncMode === "specific"
                    ? "border-zo-orange bg-zo-orange/10 ring-2 ring-zo-orange/20"
                    : "border-zo-border bg-[var(--zo-bg-subtle,rgba(0,0,0,0.02))] hover:border-zo-orange/50"
                }`}
              >
                <div className="flex w-full items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">
                    📅 Specific Date
                  </span>
                  {syncMode === "specific" && (
                    <span className="h-2.5 w-2.5 rounded-full bg-zo-orange" />
                  )}
                </div>
                <span className="mt-1 text-xs leading-relaxed text-zo-text-secondary">
                  Pick custom date
                </span>
              </button>

              {/* Option 3: All Dates */}
              <button
                type="button"
                onClick={() => {
                  setSyncMode("all");
                  setSyncDate("");
                }}
                disabled={syncing}
                className={`relative flex flex-col items-start rounded-xl border p-3 text-left transition-all ${
                  syncMode === "all"
                    ? "border-zo-orange bg-zo-orange/10 ring-2 ring-zo-orange/20"
                    : "border-zo-border bg-[var(--zo-bg-subtle,rgba(0,0,0,0.02))] hover:border-zo-orange/50"
                }`}
              >
                <div className="flex w-full items-center justify-between">
                  <span className="text-sm font-semibold text-foreground">
                    ♾️ All Dates
                  </span>
                  {syncMode === "all" && (
                    <span className="h-2.5 w-2.5 rounded-full bg-zo-orange" />
                  )}
                </div>
                <span className="mt-1 text-xs leading-relaxed text-zo-text-secondary">
                  Sync all RFPs in tab
                </span>
              </button>
            </div>
          </div>

          {/* Date Picker Input & Quick Presets */}
          {syncMode === "specific" && (
            <div className="rounded-xl border border-zo-border bg-[var(--zo-bg-subtle,rgba(0,0,0,0.02))] p-4">
              <label className="block text-xs font-semibold text-foreground">
                Target Date
              </label>
              <input
                type="date"
                value={syncDate}
                max={getTodayIso()}
                onChange={(e) => setSyncDate(e.target.value)}
                disabled={syncing}
                className="zo-input mt-2 w-full px-3 py-2 text-sm text-foreground outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
              />

              {/* Quick Presets */}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-zo-text-muted">Quick presets:</span>
                <button
                  type="button"
                  onClick={() => setSyncDate(getTodayIso())}
                  className="rounded-md border border-zo-border px-2.5 py-1 text-xs font-medium text-foreground transition-smooth hover:border-zo-orange"
                >
                  Today
                </button>
                <button
                  type="button"
                  onClick={() => setSyncDate(getPastDateIso(1))}
                  className="rounded-md border border-zo-border px-2.5 py-1 text-xs font-medium text-foreground transition-smooth hover:border-zo-orange"
                >
                  Yesterday
                </button>
                <button
                  type="button"
                  onClick={() => setSyncDate(getPastDateIso(7))}
                  className="rounded-md border border-zo-border px-2.5 py-1 text-xs font-medium text-foreground transition-smooth hover:border-zo-orange"
                >
                  7 Days Ago
                </button>
              </div>
            </div>
          )}

          {/* Tab Scope */}
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-zo-text-muted">
              JustWin Tab Scope
            </label>
            <div className="grid grid-cols-4 gap-2">
              {(["all", "hot", "warm", "review"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setTabFilter(tab)}
                  disabled={syncing}
                  className={`rounded-lg py-2 text-center text-xs font-medium capitalize transition-all ${
                    tabFilter === tab
                      ? "bg-zo-orange font-semibold text-white shadow-sm"
                      : "border border-zo-border text-zo-text-secondary hover:border-zo-orange/50 hover:text-foreground"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          {/* Progress / Status Indicator */}
          {syncing && (
            <div className="flex items-center gap-3 rounded-xl border border-zo-orange/40 bg-zo-orange/10 p-3.5 text-sm font-medium text-zo-orange">
              <div className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-zo-orange border-t-transparent" />
              <span>{statusMessage || "Syncing in progress..."}</span>
            </div>
          )}

          {/* Success Banner */}
          {successResult && (
            <div className="flex items-start gap-3 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3.5 text-emerald-600 dark:text-emerald-400">
              <svg
                className="mt-0.5 h-5 w-5 shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <div>
                <p className="text-sm font-semibold">{successResult.message}</p>
                <p className="mt-0.5 text-xs opacity-85">
                  Synced at {new Date(successResult.finishedAt!).toLocaleTimeString()}
                </p>
              </div>
            </div>
          )}

          {/* Error Banner */}
          {errorMessage && (
            <div className="flex items-start gap-3 rounded-xl border border-red-500/40 bg-red-500/10 p-3.5 text-red-600 dark:text-red-400">
              <svg
                className="mt-0.5 h-5 w-5 shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
              <p className="text-sm">{errorMessage}</p>
            </div>
          )}

          {/* Footer Actions */}
          <div className="mt-2 flex items-center justify-end gap-3 border-t border-zo-border pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={syncing}
              className="zo-btn secondary text-xs"
            >
              {successResult ? "Close" : "Cancel"}
            </button>
            {!successResult && (
              <button
                type="submit"
                disabled={syncing}
                className="zo-btn primary flex items-center gap-2 text-xs !bg-zo-orange hover:!bg-[#d44312] disabled:opacity-50"
              >
                <IconSync
                  className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`}
                />
                <span>{syncing ? "Syncing..." : "Start Sync"}</span>
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
}
