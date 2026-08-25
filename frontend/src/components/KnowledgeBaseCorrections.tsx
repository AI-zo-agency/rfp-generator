"use client";

import { useCallback, useEffect, useState } from "react";
import {
  correctionSummary,
  sortCorrections,
  type KbCorrection,
} from "@/lib/kb-corrections";

interface DraftState {
  customId: string | null;
  title: string;
  note: string;
}

const EMPTY_DRAFT: DraftState = { customId: null, title: "", note: "" };

export function KnowledgeBaseCorrections() {
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
    void load();
  }, [load]);

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
  }

  async function remove(correction: KbCorrection) {
    setDeleting(true);
    const response = await fetch(
      `/api/knowledge-base/corrections/${encodeURIComponent(correction.customId)}?documentId=${encodeURIComponent(correction.id)}`,
      { method: "DELETE" }
    );
    setDeleting(false);
    setPendingDeleteId(null);
    if (response.ok) await load();
  }

  return (
    <section className="zo-card mb-6 rounded-2xl border border-zo-border p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground">
            Standing corrections
          </h2>
          <p className="mt-1 text-sm text-zo-text-secondary">
            Notes that override older documents. Agents follow these first.
          </p>
        </div>
        <button
          type="button"
          className="zo-btn !py-2"
          onClick={() => {
            setError(null);
            setDraft(EMPTY_DRAFT);
          }}
        >
          Add note
        </button>
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-zo-text-muted">Loading corrections…</p>
      ) : corrections.length === 0 ? (
        <p className="mt-4 text-sm text-zo-text-muted">
          No standing corrections. Add one when a fact in the knowledge base goes
          out of date.
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {corrections.map((correction) => (
            <li
              key={correction.customId}
              className="flex items-start justify-between gap-4 rounded-xl border border-zo-border px-4 py-3"
            >
              <div className="min-w-0">
                <p className="text-sm text-foreground">{correctionSummary(correction)}</p>
                <p className="mt-1 text-xs text-zo-text-muted">
                  Added {correction.createdAt.slice(0, 10)}
                  {correction.linkedDocumentId ? " · filed with an upload" : ""}
                </p>
              </div>
              {pendingDeleteId === correction.customId ? (
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-xs text-zo-warning">Delete this note?</span>
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
                    {deleting ? "Deleting…" : "Confirm delete"}
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

      {draft && (
        <div className="mt-4 rounded-xl border border-zo-border p-4">
          <label className="block text-sm font-medium text-foreground">
            Correction
            <textarea
              rows={3}
              value={draft.note}
              onChange={(event) => setDraft({ ...draft, note: event.target.value })}
              placeholder='e.g. "Ron Comer has retired — do not assign him as current staff"'
              className="zo-input mt-1.5 w-full resize-y px-3 py-2.5 text-sm"
            />
          </label>
          {error && <p className="mt-2 text-sm text-zo-error">{error}</p>}
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
              className="zo-btn !py-2"
              onClick={() => void save()}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save correction"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
