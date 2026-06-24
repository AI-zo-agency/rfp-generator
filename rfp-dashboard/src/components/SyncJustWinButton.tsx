"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";
import { IconSync } from "./ui/icons";

type SyncStatus = "idle" | "running" | "done" | "error";

interface SyncJustWinButtonProps {
  variant?: "header" | "topbar" | "hero";
  className?: string;
}

export function SyncJustWinButton({
  variant = "header",
  className = "",
}: SyncJustWinButtonProps) {
  const router = useRouter();
  const [status, setStatus] = useState<SyncStatus>("idle");
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const pollStatus = useCallback(async () => {
    const response = await fetch("/api/justwin/status");
    const job = await response.json();

    if (job.status === "running") {
      setStatus("running");
      window.setTimeout(() => {
        void pollStatus();
      }, 2000);
      return;
    }

    if (job.status === "completed") {
      setStatus("done");
      setLastSynced(job.finishedAt ?? null);
      router.refresh();
      return;
    }

    if (job.status === "failed") {
      setStatus("error");
      setErrorMessage(job.error ?? "Sync failed");
    }
  }, [router]);

  useEffect(() => {
    if (!JUSTWIN_SYNC_ENABLED) return;

    void fetch("/api/justwin/status")
      .then((response) => response.json())
      .then((job) => {
        if (job.finishedAt) {
          setLastSynced(job.finishedAt);
        }
        if (job.status === "running") {
          setStatus("running");
          void pollStatus();
        }
      });
  }, [pollStatus]);

  async function handleSync() {
    if (!JUSTWIN_SYNC_ENABLED) return;

    setStatus("running");
    setErrorMessage(null);

    const response = await fetch("/api/justwin/sync", { method: "POST" });
    if (!response.ok) {
      const body = (await response.json()) as { error?: string };
      setStatus("error");
      setErrorMessage(body.error ?? "Unable to start sync");
      return;
    }

    void pollStatus();
  }

  const label = !JUSTWIN_SYNC_ENABLED
    ? "JustWin Sync (Off)"
    : status === "running"
      ? "Syncing…"
      : status === "error"
        ? "Sync Failed"
        : "Sync JustWin";

  const buttonClass =
    variant === "header"
      ? "zo-btn"
      : variant === "hero"
        ? "zo-btn w-full"
        : "zo-btn secondary !py-2 hidden sm:inline-flex";

  const isDisabled = !JUSTWIN_SYNC_ENABLED || status === "running";

  return (
    <div
      className={
        variant === "header"
          ? `flex flex-col items-end gap-3 ${className}`
          : className
      }
    >
      <button
        type="button"
        onClick={() => void handleSync()}
        disabled={isDisabled}
        title={!JUSTWIN_SYNC_ENABLED ? JUSTWIN_SYNC_DISABLED_MESSAGE : undefined}
        className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-50`}
      >
        <IconSync
          className={`sync-icon h-4 w-4 ${status === "running" ? "animate-spin" : ""}`}
        />
        {label}
      </button>
      {!JUSTWIN_SYNC_ENABLED && (
        <p className="max-w-xs text-right text-xs text-zo-text-muted">
          {JUSTWIN_SYNC_DISABLED_MESSAGE}
        </p>
      )}
      {JUSTWIN_SYNC_ENABLED && variant === "header" && lastSynced && (
        <p className="text-xs text-zo-text-muted">
          Last synced · {new Date(lastSynced).toLocaleString()}
        </p>
      )}
      {errorMessage && (
        <p className="max-w-xs text-right text-xs text-[#cf2e2e]">{errorMessage}</p>
      )}
    </div>
  );
}
