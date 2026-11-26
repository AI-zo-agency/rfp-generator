"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { UnresolvedReference } from "@/types/rfp";
import { withAuthUserEmail } from "@/lib/auth-user-email";
import { trackClick } from "@/lib/zo-analytics";
import {
  cleanPacketCiteTarget,
  deleteRfpPacketFile,
  listRfpPacketFiles,
  packetFileForCite,
  uploadRfpPacketFile,
  type PacketAnalyzeMeta,
  type RfpPacketFile,
} from "@/lib/rfp-packet-files";

type Props = {
  rfpId: string;
  references: UnresolvedReference[];
  /** Parent shows the Go/No-Go loader and polls until analysis finishes. */
  onAnalyzeStarted?: (meta: PacketAnalyzeMeta) => void;
};

const MORE_BUSY = "__more__";

function isAlreadyRunningError(message: string): boolean {
  const m = message.toLowerCase();
  return m.includes("already running") || m.includes("wait for it to finish");
}

export function UnresolvedReferenceBanner({
  rfpId,
  references,
  onAnalyzeStarted,
}: Props) {
  const [files, setFiles] = useState<RfpPacketFile[]>([]);
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  /** True after any upload/remove this session — wait for user to confirm Go/No-Go. */
  const [packetDirty, setPacketDirty] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const moreInputRef = useRef<HTMLInputElement | null>(null);

  const refreshFiles = useCallback(async () => {
    try {
      const next = await listRfpPacketFiles(rfpId);
      setFiles(next);
    } catch {
      // Banner still works for upload even if list fails once.
    }
  }, [rfpId]);

  useEffect(() => {
    void refreshFiles();
  }, [refreshFiles]);

  async function handleFileChosen(
    item: UnresolvedReference,
    fileList: FileList | null
  ) {
    const file = fileList?.[0];
    if (!file) return;
    // Already attached on this row — do not replace via the locked control.
    if (packetFileForCite(files, item.target)) return;

    const citeTarget = cleanPacketCiteTarget(item.target) || item.target;
    setError(null);
    setSyncNote(null);
    setBusyTarget(item.target);
    try {
      await uploadRfpPacketFile(rfpId, {
        citeTarget,
        citeQuote: item.quote,
        file,
      });
      setBusyTarget(null);
      setPacketDirty(true);
      setSyncNote(
        "Saved on this RFP. Add any other missing PDFs, then confirm Go/No-Go below."
      );
      await refreshFiles();
    } catch (err) {
      setBusyTarget(null);
      const message = err instanceof Error ? err.message : "Upload failed";
      if (isAlreadyRunningError(message)) {
        setError(null);
        setSyncNote(
          "Go/No-Go is already running — syncing that run. Add the attachment again when it finishes."
        );
        onAnalyzeStarted?.({
          citeTarget,
          action: "upload",
          uploadBlocked: true,
        });
        return;
      }
      setError(message);
    }
  }

  async function handleAddMore(fileList: FileList | null) {
    const chosen = fileList ? Array.from(fileList) : [];
    if (!chosen.length) return;
    setError(null);
    setSyncNote(null);
    setBusyTarget(MORE_BUSY);
    let saved = 0;
    try {
      for (const file of chosen) {
        const citeTarget = cleanPacketCiteTarget(file.name) || file.name;
        await uploadRfpPacketFile(rfpId, {
          citeTarget,
          citeQuote: `Additional packet file: ${citeTarget}`,
          file,
        });
        saved += 1;
      }
      setBusyTarget(null);
      setPacketDirty(true);
      setSyncNote(
        saved === 1
          ? "Extra PDF saved on this RFP. Confirm Go/No-Go below when the packet is ready."
          : `${saved} extra PDFs saved on this RFP. Confirm Go/No-Go below when the packet is ready.`
      );
      await refreshFiles();
    } catch (err) {
      setBusyTarget(null);
      const message = err instanceof Error ? err.message : "Upload failed";
      if (isAlreadyRunningError(message)) {
        setError(null);
        setSyncNote(
          "Go/No-Go is already running — syncing that run. Add more documents when it finishes."
        );
        onAnalyzeStarted?.({
          citeTarget: "",
          action: "upload",
          uploadBlocked: true,
        });
        return;
      }
      setError(
        saved > 0
          ? `${message} (${saved} file${saved === 1 ? "" : "s"} already saved.)`
          : message
      );
      await refreshFiles();
    }
  }

  async function handleRemove(file: RfpPacketFile) {
    if (
      !window.confirm(
        `Remove “${file.filename}” for ${file.citeTarget} on this RFP only?`
      )
    ) {
      return;
    }
    setError(null);
    setSyncNote(null);
    setBusyTarget(file.citeTarget);
    try {
      await deleteRfpPacketFile(rfpId, file.id);
      setBusyTarget(null);
      setPacketDirty(true);
      setSyncNote(
        "Removed from this RFP. Confirm Go/No-Go below when your packet is ready."
      );
      await refreshFiles();
    } catch (err) {
      setBusyTarget(null);
      const message = err instanceof Error ? err.message : "Remove failed";
      if (isAlreadyRunningError(message)) {
        setError(null);
        setSyncNote(
          "Go/No-Go is already running — syncing that run. Remove again when it finishes."
        );
        onAnalyzeStarted?.({
          citeTarget: file.citeTarget,
          action: "delete",
          uploadBlocked: true,
        });
        return;
      }
      setError(message);
    }
  }

  async function handleConfirmGoNoGo() {
    if (
      !window.confirm(
        "Update Go/No-Go with the attachments you added? This replaces the current analysis and uses AI credit."
      )
    ) {
      return;
    }
    setError(null);
    setSyncNote(null);
    setConfirming(true);
    trackClick("rfp.gonogo_run", {
      path: `/rfps/${rfpId}`,
      entity_type: "rfp",
      entity_id: rfpId,
    });
    try {
      const res = await fetch(`/api/rfps/${encodeURIComponent(rfpId)}/analyze`, {
        method: "POST",
        headers: withAuthUserEmail(),
      });
      const data = (await res.json()) as {
        error?: string;
        detail?: string;
        status?: string;
      };
      if (!res.ok) {
        const message = data.detail ?? data.error ?? "Could not start Go/No-Go";
        if (isAlreadyRunningError(message)) {
          setPacketDirty(false);
          onAnalyzeStarted?.({
            citeTarget: "",
            action: "confirm_update",
            uploadBlocked: true,
          });
          return;
        }
        setError(message);
        return;
      }
      setPacketDirty(false);
      onAnalyzeStarted?.({ citeTarget: "", action: "confirm_update" });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not start Go/No-Go"
      );
    } finally {
      setConfirming(false);
    }
  }

  if (!references.length) return null;

  const busy = busyTarget !== null || confirming;
  const attachedCount = references.filter((item) =>
    Boolean(packetFileForCite(files, item.target))
  ).length;
  const listedIds = new Set(
    references
      .map((item) => packetFileForCite(files, item.target)?.id)
      .filter((id): id is string => Boolean(id))
  );
  const extraFiles = files.filter((f) => !listedIds.has(f.id));

  return (
    <div className="rounded-xl border border-zo-orange/40 bg-zo-orange/5 p-5">
      <h3 className="font-heading text-sm font-bold uppercase tracking-wide text-zo-orange">
        Missing attachments cited in this RFP
      </h3>
      <p className="mt-2 text-sm text-zo-text-secondary">
        Add each cited PDF once on its row — attached rows stay locked. Use Add
        more documents below for anything else. Files stay on this RFP only;
        confirm Go/No-Go when the packet looks complete (nothing runs on each
        upload).
      </p>
      {error ? (
        <p className="mt-2 text-sm text-zo-error" role="alert">
          {error}
        </p>
      ) : null}
      {syncNote ? (
        <p className="mt-2 text-sm text-zo-text-secondary" role="status">
          {syncNote}
        </p>
      ) : null}
      <ul className="mt-3 space-y-2">
        {references.map((item) => {
          const attached = packetFileForCite(files, item.target);
          const rowBusy = busyTarget === item.target;
          const displayTarget =
            cleanPacketCiteTarget(item.target) || item.target;
          return (
            <li
              key={`${item.target}-${item.quote}`}
              className="rounded-lg border border-zo-orange/30 bg-white/70 px-3 py-2 text-sm text-zo-text-secondary"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-foreground">{displayTarget}</p>
                  <p className="mt-1">{item.quote}</p>
                  {attached ? (
                    <p className="mt-1 text-xs text-zo-orange">
                      Attached: {attached.filename}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
                  {!attached ? (
                    <>
                      <input
                        ref={(el) => {
                          inputRefs.current[item.target] = el;
                        }}
                        type="file"
                        accept="application/pdf,.pdf"
                        className="hidden"
                        onChange={(e) => {
                          void handleFileChosen(item, e.target.files);
                          e.target.value = "";
                        }}
                      />
                      <button
                        type="button"
                        className="zo-btn secondary text-xs disabled:opacity-60"
                        disabled={busy}
                        onClick={() =>
                          inputRefs.current[item.target]?.click()
                        }
                      >
                        {rowBusy ? "Working…" : "Add attachment"}
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="rounded-full border border-zo-orange/40 bg-zo-orange/10 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-zo-orange">
                        Attached
                      </span>
                      <button
                        type="button"
                        className="text-xs font-medium text-zo-error underline-offset-2 hover:underline disabled:opacity-60"
                        disabled={busy}
                        onClick={() => void handleRemove(attached)}
                      >
                        Remove
                      </button>
                    </>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {extraFiles.length > 0 ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
            Also on this RFP
          </p>
          <ul className="space-y-2">
            {extraFiles.map((file) => (
              <li
                key={file.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zo-border bg-white/70 px-3 py-2 text-sm text-zo-text-secondary"
              >
                <span className="min-w-0 truncate font-medium text-foreground">
                  {file.filename}
                </span>
                <button
                  type="button"
                  className="text-xs font-medium text-zo-error underline-offset-2 hover:underline disabled:opacity-60"
                  disabled={busy}
                  onClick={() => void handleRemove(file)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <input
          ref={moreInputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="hidden"
          onChange={(e) => {
            void handleAddMore(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="zo-btn secondary text-xs disabled:opacity-60"
          disabled={busy}
          onClick={() => moreInputRef.current?.click()}
        >
          {busyTarget === MORE_BUSY ? "Uploading…" : "Add more documents"}
        </button>
        <p className="text-xs text-zo-text-muted">
          Extra PDFs not listed above (addenda, workbooks, etc.)
        </p>
      </div>

      {packetDirty ? (
        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-zo-orange/40 bg-white/80 px-3 py-3"
          role="status"
        >
          <p className="text-sm text-zo-text-secondary">
            {attachedCount > 0 || files.length > 0
              ? `${Math.max(attachedCount, files.length)} attachment${
                  Math.max(attachedCount, files.length) === 1 ? "" : "s"
                } on this RFP. Update Go/No-Go when you’re done adding files?`
              : "Packet changed. Update Go/No-Go when you’re ready?"}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="text-xs font-medium text-zo-text-muted underline-offset-2 hover:underline disabled:opacity-60"
              disabled={confirming}
              onClick={() => {
                setPacketDirty(false);
                setSyncNote(null);
              }}
            >
              Not now
            </button>
            <button
              type="button"
              className="zo-btn text-xs disabled:opacity-60"
              disabled={busy}
              onClick={() => void handleConfirmGoNoGo()}
            >
              {confirming ? "Starting…" : "Update Go/No-Go"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
