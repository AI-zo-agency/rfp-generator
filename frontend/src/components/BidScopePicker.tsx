"use client";

import { useCallback, useMemo, useState } from "react";
import type { GoNoGoAnalysis } from "@/types/rfp";
import {
  availableTracksFromAnalysis,
  bidScopeNeedsLock,
} from "@/lib/bid-scope";

interface BidScopePickerProps {
  rfpId: string;
  analysis: GoNoGoAnalysis | null | undefined;
  selectedTracks?: string[];
  bidScopeLockedAt?: string | null;
  onLocked?: (payload: {
    selectedTracks: string[];
    bidScopeLockedAt: string | null;
  }) => void;
}

export function BidScopePicker({
  rfpId,
  analysis,
  selectedTracks = [],
  bidScopeLockedAt = null,
  onLocked,
}: BidScopePickerProps) {
  const tracks = useMemo(
    () => availableTracksFromAnalysis(analysis),
    [analysis],
  );
  const needsLock = bidScopeNeedsLock(tracks);
  const isLocked = Boolean(bidScopeLockedAt && selectedTracks.length > 0);

  const [draft, setDraft] = useState<string[]>(() =>
    selectedTracks.length ? [...selectedTracks] : [...tracks],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  if (!needsLock) return null;

  const showPicker = !isLocked || editing;

  const toggle = useCallback((label: string) => {
    setDraft((prev) =>
      prev.includes(label)
        ? prev.filter((t) => t !== label)
        : [...prev, label],
    );
  }, []);

  const lockScope = useCallback(async () => {
    if (draft.length === 0) {
      setError("Select at least one track (or all tracks).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/rfps/${encodeURIComponent(rfpId)}/bid-scope`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selectedTracks: draft,
          invalidateArtifacts: true,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        selectedTracks?: string[];
        bidScopeLockedAt?: string | null;
        error?: string;
        detail?: string;
      };
      if (!res.ok) {
        throw new Error(
          data.error || data.detail || `Failed to lock bid scope (${res.status})`,
        );
      }
      onLocked?.({
        selectedTracks: data.selectedTracks ?? draft,
        bidScopeLockedAt: data.bidScopeLockedAt ?? new Date().toISOString(),
      });
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to lock bid scope");
    } finally {
      setBusy(false);
    }
  }, [draft, onLocked, rfpId]);

  return (
    <section className="zo-card mt-4 space-y-3 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold text-foreground">Bid scope</h3>
          <p className="mt-1 text-xs text-zo-text-muted">
            This RFP has multiple tracks/roles. Choose which to bid before Build.
            Shared requirements (insurance, forms, submission) always stay in.
          </p>
        </div>
        {isLocked && !editing ? (
          <span className="rounded-md border border-zo-border bg-white px-2.5 py-1 text-[11px] font-semibold text-zo-text-secondary">
            Bidding: {selectedTracks.join(", ")}
          </span>
        ) : null}
      </div>

      {showPicker ? (
        <>
          <ul className="space-y-2">
            {tracks.map((label) => (
              <li key={label}>
                <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    checked={draft.includes(label)}
                    onChange={() => toggle(label)}
                    disabled={busy}
                    className="h-4 w-4 rounded border-zo-border"
                  />
                  <span>{label}</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => setDraft([...tracks])}
              className="rounded-lg border border-zo-border bg-white px-3 py-1.5 text-xs font-semibold text-zo-text-secondary hover:bg-black/[0.03] disabled:opacity-50"
            >
              Select all tracks
            </button>
            <button
              type="button"
              disabled={busy || draft.length === 0}
              onClick={() => void lockScope()}
              className="zo-btn disabled:opacity-60"
            >
              {busy ? "Saving…" : "Lock bid scope"}
            </button>
            {editing ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setDraft(
                    selectedTracks.length ? [...selectedTracks] : [...tracks],
                  );
                  setError(null);
                }}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold text-zo-text-muted"
              >
                Cancel
              </button>
            ) : null}
          </div>
        </>
      ) : (
        <button
          type="button"
          className="text-xs font-semibold text-[#ef5018] hover:underline"
          onClick={() => {
            const ok = window.confirm(
              "Changing bid scope clears the current structural map and proposal draft. You will need to run Build again. Continue?",
            );
            if (!ok) return;
            setDraft(selectedTracks.length ? [...selectedTracks] : [...tracks]);
            setEditing(true);
          }}
        >
          Change…
        </button>
      )}

      {error ? (
        <p className="text-xs font-medium text-zo-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
