"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { UnresolvedReference } from "@/types/rfp";
import {
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
  /** Parent shows the Fit loader and polls until analysis finishes. */
  onAnalyzeStarted?: (meta: PacketAnalyzeMeta) => void;
};

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
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});

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
    setError(null);
    setSyncNote(null);
    setBusyTarget(item.target);
    try {
      await uploadRfpPacketFile(rfpId, {
        citeTarget: item.target,
        citeQuote: item.quote,
        file,
      });
      setBusyTarget(null);
      await refreshFiles();
      onAnalyzeStarted?.({ citeTarget: item.target, action: "upload" });
    } catch (err) {
      setBusyTarget(null);
      const message = err instanceof Error ? err.message : "Upload failed";
      if (isAlreadyRunningError(message)) {
        // Reflect the live Fit job in the parent UI instead of a red error
        // next to completed scores. File was NOT saved — retry after Fit ends.
        setError(null);
        setSyncNote(
          "Fit is already updating — syncing that run. Add the attachment again when it finishes."
        );
        onAnalyzeStarted?.({
          citeTarget: item.target,
          action: "upload",
          uploadBlocked: true,
        });
        return;
      }
      setError(message);
    }
  }

  async function handleRemove(file: RfpPacketFile) {
    if (
      !window.confirm(
        `Remove “${file.filename}” for ${file.citeTarget} on this RFP only? Fit will run again.`
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
      await refreshFiles();
      onAnalyzeStarted?.({ citeTarget: file.citeTarget, action: "delete" });
    } catch (err) {
      setBusyTarget(null);
      const message = err instanceof Error ? err.message : "Remove failed";
      if (isAlreadyRunningError(message)) {
        setError(null);
        setSyncNote(
          "Fit is already updating — syncing that run. Remove again when it finishes."
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

  if (!references.length) return null;

  return (
    <div className="rounded-xl border border-zo-orange/40 bg-zo-orange/5 p-5">
      <h3 className="font-heading text-sm font-bold uppercase tracking-wide text-zo-orange">
        Missing attachments cited in this RFP
      </h3>
      <p className="mt-2 text-sm text-zo-text-secondary">
        The solicitation points at these parts of itself, but their text is not
        in the uploaded file. Add the missing PDF on a row — it stays attached
        to this RFP only. Fit refreshes from that attachment only; if a draft
        already exists, only the tabs that depend on that exhibit are updated.
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
          const busy = busyTarget === item.target;
          return (
            <li
              key={`${item.target}-${item.quote}`}
              className="rounded-lg border border-zo-orange/30 bg-white/70 px-3 py-2 text-sm text-zo-text-secondary"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-foreground">{item.target}</p>
                  <p className="mt-1">{item.quote}</p>
                  {attached ? (
                    <p className="mt-1 text-xs text-zo-orange">
                      Attached to this RFP: {attached.filename} — still flagged
                      because Fit does not see this cite’s body in that PDF.
                      Replace with the real exhibit.
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
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
                    onClick={() => inputRefs.current[item.target]?.click()}
                  >
                    {busy
                      ? "Working…"
                      : attached
                        ? "Replace PDF"
                        : "Add attachment"}
                  </button>
                  {attached ? (
                    <button
                      type="button"
                      className="text-xs font-medium text-zo-error underline-offset-2 hover:underline disabled:opacity-60"
                      disabled={busy}
                      onClick={() => void handleRemove(attached)}
                    >
                      Remove
                    </button>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
