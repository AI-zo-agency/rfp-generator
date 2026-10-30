"use client";

import { useCallback, useEffect, useState } from "react";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";
import { SyncJustWinModal } from "./SyncJustWinModal";
import { IconSync } from "./ui/icons";

async function fetchSyncStatus(): Promise<{
  status?: string;
  finishedAt?: string;
} | null> {
  try {
    const res = await fetch("/api/justwin/status", { cache: "no-store" });
    return res.ok ? await res.json() : null;
  } catch {
    // Ignore status fetch errors
    return null;
  }
}

interface SyncJustWinButtonProps {
  variant?: "header" | "topbar" | "hero";
  className?: string;
}

export function SyncJustWinButton({
  variant = "header",
  className = "",
}: SyncJustWinButtonProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  // The modal reports while it follows a sync; the server is asked directly
  // for one this page didn't start (another button, tab, or person).
  const [modalSyncing, setModalSyncing] = useState(false);
  const [serverRunning, setServerRunning] = useState(false);
  const syncingNow = modalSyncing || serverRunning;

  const checkStatus = useCallback(() => {
    void fetchSyncStatus().then((job) => {
      if (!job) return;
      setServerRunning(job.status === "running");
      if (job.finishedAt) setLastSynced(job.finishedAt);
    });
  }, []);

  // On load, and whenever the modal stops following a sync (finished,
  // failed, or the page changed), confirm with the server instead of guessing.
  useEffect(() => {
    if (!JUSTWIN_SYNC_ENABLED || modalSyncing) return;
    checkStatus();
  }, [modalSyncing, checkStatus]);

  // A sync this page didn't start: the modal isn't polling it, so poll here
  // until the server says it's done.
  useEffect(() => {
    if (!serverRunning || modalSyncing) return;
    const id = window.setInterval(checkStatus, 5000);
    return () => window.clearInterval(id);
  }, [serverRunning, modalSyncing, checkStatus]);

  function handleOpenModal() {
    if (!JUSTWIN_SYNC_ENABLED) return;
    setModalOpen(true);
  }

  function handleSyncSuccess() {
    setLastSynced(new Date().toISOString());
  }

  const label = !JUSTWIN_SYNC_ENABLED
    ? "JustWin Sync (Off)"
    : syncingNow
      ? "Syncing JustWin…"
      : "Sync JustWin";

  const buttonClass =
    variant === "header"
      ? "zo-btn"
      : variant === "hero"
        ? "zo-btn w-full !bg-[#ef5018] !text-white hover:!bg-[#d44312]"
        : "zo-btn secondary !py-2 hidden sm:inline-flex";

  return (
    <>
      <div
        className={
          variant === "header"
            ? `flex flex-col items-end gap-1.5 ${className}`
            : className
        }
      >
        <button
          type="button"
          onClick={handleOpenModal}
          disabled={!JUSTWIN_SYNC_ENABLED}
          title={
            !JUSTWIN_SYNC_ENABLED
              ? JUSTWIN_SYNC_DISABLED_MESSAGE
              : syncingNow
                ? "A JustWin sync is running — click to see its progress"
                : undefined
          }
          className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center gap-2 font-medium`}
        >
          <IconSync className={`h-4 w-4 shrink-0 ${syncingNow ? "animate-spin" : ""}`} />
          <span>{label}</span>
        </button>
        {!JUSTWIN_SYNC_ENABLED && (
          <p className="max-w-xs text-right text-xs text-zo-text-muted">
            {JUSTWIN_SYNC_DISABLED_MESSAGE}
          </p>
        )}
        {JUSTWIN_SYNC_ENABLED && variant === "header" && syncingNow && (
          <p className="text-xs font-medium text-zo-orange">
            Running in the background · click to see progress
          </p>
        )}
        {JUSTWIN_SYNC_ENABLED && variant === "header" && !syncingNow && lastSynced && (
          <p className="text-xs text-zo-text-muted">
            Last synced · {new Date(lastSynced).toLocaleString()}
          </p>
        )}
      </div>

      <SyncJustWinModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={handleSyncSuccess}
        onSyncingChange={setModalSyncing}
      />
    </>
  );
}
