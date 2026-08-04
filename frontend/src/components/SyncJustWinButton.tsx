"use client";

import { useEffect, useState } from "react";
import {
  JUSTWIN_SYNC_DISABLED_MESSAGE,
  JUSTWIN_SYNC_ENABLED,
} from "@/lib/justwin-config";
import { SyncJustWinModal } from "./SyncJustWinModal";
import { IconSync } from "./ui/icons";

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

  useEffect(() => {
    if (!JUSTWIN_SYNC_ENABLED) return;

    void fetch("/api/justwin/status")
      .then((res) => res.json())
      .then((job) => {
        if (job.finishedAt) {
          setLastSynced(job.finishedAt);
        }
      })
      .catch(() => {
        // Ignore status fetch errors
      });
  }, []);

  function handleOpenModal() {
    if (!JUSTWIN_SYNC_ENABLED) return;
    setModalOpen(true);
  }

  function handleSyncSuccess() {
    setLastSynced(new Date().toISOString());
  }

  const label = !JUSTWIN_SYNC_ENABLED ? "JustWin Sync (Off)" : "Sync JustWin";

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
          title={!JUSTWIN_SYNC_ENABLED ? JUSTWIN_SYNC_DISABLED_MESSAGE : undefined}
          className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center gap-2 font-medium`}
        >
          <IconSync className="h-4 w-4 shrink-0" />
          <span>{label}</span>
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
      </div>

      <SyncJustWinModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={handleSyncSuccess}
      />
    </>
  );
}
