"use client";

import { useEffect, useState } from "react";
import {
  errorMessage,
  pinSelection,
  voiceOptions,
  type BrandVoiceRevisionList,
  type ProposalVoiceRev,
} from "@/lib/brand-voice";

/** Toolbar control: which standards revision this proposal is written under. */
export function VoiceRevChip({ rfpId }: { rfpId: string }) {
  const [list, setList] = useState<BrandVoiceRevisionList | null>(null);
  const [pin, setPin] = useState<ProposalVoiceRev | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [revs, current] = await Promise.all([
          fetch("/api/brand-voice/revisions", { cache: "no-store" }),
          fetch(`/api/rfps/${encodeURIComponent(rfpId)}/proposal/voice-rev`, { cache: "no-store" }),
        ]);
        if (!alive || !revs.ok || !current.ok) return;
        const [nextList, nextPin] = await Promise.all([revs.json(), current.json()]);
        if (!alive) return;
        setList(nextList);
        setPin(nextPin);
      } catch {
        // The control is optional. Stay hidden if the backend is unreachable.
      }
    })();
    return () => {
      alive = false;
    };
  }, [rfpId]);

  // Nothing to choose between.
  if (!list || !pin || list.revisions.length < 2) return null;

  async function change(revisionId: string) {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/rfps/${encodeURIComponent(rfpId)}/proposal/voice-rev`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revisionId }),
      });
      if (res.ok) setPin(await res.json());
      else setError(errorMessage(await res.json().catch(() => null), res.status));
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Could not change the voice revision");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label
        className="inline-flex min-h-[2.125rem] items-center gap-1.5 rounded-lg border border-zo-border/80 bg-white px-2.5 py-1.5 text-xs font-semibold text-zo-text-secondary"
        title="Brand voice revision this proposal is written under. Changing it affects the next prompts; voice review restarts on the next pass."
      >
        <span className="hidden text-zo-text-muted sm:inline">Voice</span>
        <select
          aria-label="Brand voice revision"
          value={pinSelection(pin)}
          disabled={busy}
          onChange={(e) => void change(e.target.value)}
          className="bg-transparent text-foreground outline-none"
        >
          {voiceOptions(list, pin).map((rev) => (
            <option key={rev.id} value={rev.id}>
              {rev.label}
            </option>
          ))}
        </select>
      </label>
      {error ? (
        <span role="alert" className="text-xs text-red-600">
          {error}
        </span>
      ) : null}
    </>
  );
}
