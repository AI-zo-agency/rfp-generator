"use client";

import { Dismiss24Regular } from "@fluentui/react-icons";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  correctionSummary,
  sortCorrections,
  type KbCorrection,
} from "@/lib/kb-corrections";
import { kbBtnPrimary, kbBtnSecondary } from "@/lib/kb-brand";

interface DraftState {
  customId: string | null;
  title: string;
  note: string;
}

const EMPTY_DRAFT: DraftState = { customId: null, title: "", note: "" };

interface KnowledgeBaseCorrectionsModalProps {
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
}

export function KnowledgeBaseCorrectionsModal({
  open,
  onClose,
  onChanged,
}: KnowledgeBaseCorrectionsModalProps) {
  const [mounted, setMounted] = useState(false);
  const [corrections, setCorrections] = useState<KbCorrection[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/knowledge-base/corrections");
      if (response.ok) {
        const body = (await response.json()) as { corrections?: KbCorrection[] };
        setCorrections(sortCorrections(body.corrections ?? []));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    setDraft(null);
    setError(null);
    setPendingDeleteId(null);
    void load();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving && !deleting) onClose();
    };
    globalThis.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      globalThis.removeEventListener("keydown", onKeyDown);
    };
  }, [open, load, onClose, saving, deleting]);

  async function save() {
    if (!draft || !draft.note.trim()) {
      setError("Write the correction before saving.");
      return;
    }
    setSaving(true);
    setError(null);

    const editing = Boolean(draft.customId);
    const url = editing
      ? `/api/knowledge-base/corrections/${encodeURIComponent(draft.customId as string)}`
      : "/api/knowledge-base/corrections";

    const response = await fetch(url, {
      method: editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: draft.title, note: draft.note }),
    });

    setSaving(false);
    if (!response.ok) {
      const body = (await response.json()) as { detail?: string; error?: string };
      setError(body.detail ?? body.error ?? "Could not save the correction.");
      return;
    }
    setDraft(null);
    await load();
    onChanged?.();
  }

  async function remove(correction: KbCorrection) {
    setDeleting(true);
    const response = await fetch(
      `/api/knowledge-base/corrections/${encodeURIComponent(correction.customId)}?documentId=${encodeURIComponent(correction.id)}`,
      { method: "DELETE" }
    );
    setDeleting(false);
    setPendingDeleteId(null);
    if (response.ok) {
      await load();
      onChanged?.();
    }
  }

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="kb-corrections-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Close dialog"
        onClick={onClose}
      />

      <div className="zo-card relative z-10 flex max-h-[min(90dvh,760px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-zo-border bg-[var(--zo-card-bg)] shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-zo-border px-6 py-5 md:px-8">
          <div className="min-w-0 pr-2">
            <p className="text-[11px] uppercase tracking-[0.28em] text-[#ef5018]">
              Standing corrections
            </p>
            <h2
              id="kb-corrections-title"
              className="font-heading mt-2 text-2xl font-semibold text-foreground"
            >
              Agency notes
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
              Notes that override older documents. Agents follow these first.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shell-icon-btn flex h-9 w-9 shrink-0 items-center justify-center"
            aria-label="Close"
          >
            <Dismiss24Regular className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex shrink-0 justify-end border-b border-zo-border px-6 py-3 md:px-8">
            <button
              type="button"
              className={`${kbBtnSecondary} !py-2`}
              onClick={() => {
                setError(null);
                setDraft(EMPTY_DRAFT);
              }}
            >
              + Add note
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4 md:px-8">
            {loading ? (
              <p className="text-sm text-zo-text-muted">Loading corrections…</p>
            ) : corrections.length === 0 ? (
              <p className="text-sm text-zo-text-muted">
                No standing corrections yet. Add one when a fact in the knowledge
                base goes out of date.
              </p>
            ) : (
              <ul className="space-y-2">
                {corrections.map((correction) => (
                  <li
                    key={correction.customId}
                    className="flex items-start justify-between gap-4 rounded-xl border border-zo-border px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm text-foreground">
                        {correctionSummary(correction)}
                      </p>
                      <p className="mt-1 text-xs text-zo-text-muted">
                        Added {correction.createdAt.slice(0, 10)}
                        {correction.linkedDocumentId ? " · filed with an upload" : ""}
                      </p>
                    </div>
                    {pendingDeleteId === correction.customId ? (
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="text-xs text-zo-warning">Delete?</span>
                        <button
                          type="button"
                          className="zo-btn secondary !py-1.5 !text-xs"
                          onClick={() => setPendingDeleteId(null)}
                          disabled={deleting}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="zo-btn !py-1.5 !text-xs"
                          onClick={() => void remove(correction)}
                          disabled={deleting}
                        >
                          {deleting ? "Deleting…" : "Confirm"}
                        </button>
                      </div>
                    ) : (
                      <div className="flex shrink-0 gap-2">
                        <button
                          type="button"
                          className="zo-btn secondary !py-1.5 !text-xs"
                          onClick={() => {
                            setError(null);
                            setDraft({
                              customId: correction.customId,
                              title: correction.title,
                              note: correction.note,
                            });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="zo-btn secondary !py-1.5 !text-xs"
                          onClick={() => setPendingDeleteId(correction.customId)}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {draft ? (
              <div className="mt-4 rounded-xl border border-zo-border bg-zo-warm-gray/20 p-4">
                <label className="block text-sm font-medium text-foreground">
                  {draft.customId ? "Edit correction" : "New correction"}
                  <textarea
                    rows={3}
                    value={draft.note}
                    onChange={(event) =>
                      setDraft({ ...draft, note: event.target.value })
                    }
                    placeholder='e.g. "Ron Comer has retired — do not assign him as current staff"'
                    className="zo-input mt-1.5 w-full resize-y px-3 py-2.5 text-sm"
                  />
                </label>
                {error ? <p className="mt-2 text-sm text-zo-error">{error}</p> : null}
                <div className="mt-3 flex justify-end gap-2">
                  <button
                    type="button"
                    className="zo-btn secondary !py-2"
                    onClick={() => setDraft(null)}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className={`${kbBtnPrimary} !py-1.5 !text-xs`}
                    onClick={() => void save()}
                    disabled={saving}
                  >
                    {saving ? "Saving…" : "Save correction"}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

export async function fetchCorrectionCount(): Promise<number> {
  try {
    const response = await fetch("/api/knowledge-base/corrections");
    if (!response.ok) return 0;
    const body = (await response.json()) as { corrections?: KbCorrection[] };
    return body.corrections?.length ?? 0;
  } catch {
    return 0;
  }
}
