"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { TweakDiff } from "@/components/TweakDiff";
import { formatRelativeTime } from "@/lib/format";
import {
  fetchLedgerDetail,
  fetchTweakDetail,
  type LedgerDetail,
  type LedgerRun,
  type TweakDetail,
  type TweakSummary,
} from "@/lib/rfp-ledger-api";
import {
  bucketLabel,
  formatCost,
  formatWordDelta,
  outcomeLabel,
  prettyPhase,
  sourceLabel,
} from "@/lib/rfp-ledger";

type TimelineItem =
  | { at: string; kind: "run"; run: LedgerRun }
  | { at: string; kind: "tweak"; tweak: TweakSummary }
  | { at: string; kind: "open"; actor: string };

const CHIP = "rounded px-1.5 py-0.5 text-[10px] font-semibold bg-black/[0.05] text-zo-text-secondary";

function TweakRow({ tweak }: { tweak: TweakSummary }) {
  const [detail, setDetail] = useState<TweakDetail | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (detail) return;
    try {
      setDetail(await fetchTweakDetail(tweak.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the change");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold text-foreground">
          {tweak.actor_email} · {sourceLabel(tweak.source)}
        </span>
        <span className="text-zo-text-secondary">
          {tweak.kind === "added" || tweak.kind === "removed" || tweak.kind === "renamed"
            ? `${tweak.kind} section “${tweak.section_title}”`
            : `“${tweak.section_title}”`}
        </span>
        <span className={CHIP}>{bucketLabel(tweak.bucket)}</span>
        <span className="text-xs tabular-nums text-zo-text-muted">
          {formatWordDelta(tweak.words_added, tweak.words_removed)}
        </span>
        <button
          type="button"
          onClick={() => void toggle()}
          className="ml-auto rounded-md border border-black/10 px-2 py-1 text-[11px] font-semibold text-zo-text-secondary hover:bg-black/[0.03]"
        >
          {open ? "Hide change" : "Show change"}
        </button>
      </div>
      {open ? (
        error ? (
          <p className="text-xs text-red-600">{error}</p>
        ) : detail ? (
          <div className="space-y-2">
            {detail.instruction ? (
              <p className="rounded-lg bg-black/[0.03] px-3 py-2 text-xs text-zo-text-secondary">
                <span className="font-semibold">Instruction typed:</span> {detail.instruction}
              </p>
            ) : null}
            {detail.kind === "renamed" ? (
              <p className="text-xs text-zo-text-secondary">
                Renamed “{detail.before_title}” → “{detail.after_title}”
              </p>
            ) : (
              <TweakDiff before={detail.before_text} after={detail.after_text} />
            )}
          </div>
        ) : (
          <p className="text-xs text-zo-text-muted animate-pulse">Loading change…</p>
        )
      ) : null}
    </div>
  );
}

function RunRow({ run }: { run: LedgerRun }) {
  const title = run.kind === "gonogo" ? "Go/No-Go" : "Proposal run";
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold text-foreground">
          {run.owner} · {title}
        </span>
        <span className={CHIP}>{outcomeLabel(run.outcome)}</span>
        {run.duration_sec !== null ? (
          <span className="text-xs tabular-nums text-zo-text-muted">
            {Math.max(1, Math.round(run.duration_sec / 60))} min
          </span>
        ) : null}
        <span className="text-xs tabular-nums text-zo-text-muted">{formatCost(run.cost_usd)}</span>
        {run.stopped_by ? (
          <span className="text-xs text-zo-text-muted">stopped by {run.stopped_by}</span>
        ) : null}
      </div>
      {run.phases.length > 0 && run.kind === "proposal" ? (
        <p className="text-xs text-zo-text-muted">
          {run.phases.map((p) => `${prettyPhase(p.phase)} (${outcomeLabel(p.outcome).toLowerCase()})`).join(" → ")}
        </p>
      ) : null}
    </div>
  );
}

export function RfpLedgerDetail({ rfpId, onBack }: { rfpId: string; onBack: () => void }) {
  const [showOpens, setShowOpens] = useState(false);
  // The last good result stays on screen while a toggle refetches; an error
  // only counts for the request that produced it.
  const requestKey = `${rfpId}|${showOpens}`;
  const [loaded, setLoaded] = useState<{
    key: string;
    detail: LedgerDetail | null;
    error: string | null;
  }>({ key: "", detail: null, error: null });
  const detail = loaded.detail;
  const error = loaded.key === requestKey ? loaded.error : null;

  useEffect(() => {
    let cancelled = false;
    fetchLedgerDetail(rfpId, showOpens).then(
      (data) => {
        if (!cancelled) setLoaded({ key: requestKey, detail: data, error: null });
      },
      (err) => {
        if (!cancelled) {
          setLoaded((prev) => ({
            key: requestKey,
            detail: prev.detail,
            error: err instanceof Error ? err.message : "Failed to load this RFP",
          }));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [rfpId, showOpens, requestKey]);

  const timeline = useMemo<TimelineItem[]>(() => {
    if (!detail) return [];
    const items: TimelineItem[] = [
      ...detail.runs.map((run) => ({ at: run.started_at, kind: "run" as const, run })),
      ...detail.tweaks.map((tweak) => ({ at: tweak.created_at, kind: "tweak" as const, tweak })),
      ...detail.opens.map((o) => ({ at: o.created_at, kind: "open" as const, actor: o.actor_email })),
    ];
    return items.sort((a, b) => (a.at < b.at ? 1 : -1));
  }, [detail]);

  const rfp = detail?.rfp ?? null;

  return (
    <div className="space-y-5">
      <button
        type="button"
        onClick={onBack}
        className="text-xs font-semibold text-zo-teal hover:text-zo-orange"
      >
        ← All RFPs
      </button>
      {error ? (
        <p className="text-sm text-red-600">{error}</p>
      ) : !detail ? (
        <p className="text-sm text-zo-text-muted animate-pulse">Loading…</p>
      ) : !rfp ? (
        <p className="text-sm text-zo-text-muted">Nothing recorded for this RFP yet.</p>
      ) : (
        <>
          <div>
            <h3 className="font-heading text-lg font-semibold text-foreground">{rfp.title}</h3>
            {rfp.deleted ? (
              <p className="mt-1 text-xs font-semibold text-red-700">
                Deleted by {rfp.deleted_by} · {formatRelativeTime(rfp.deleted_at ?? "")}
              </p>
            ) : (
              <Link
                href={`/rfps/${encodeURIComponent(rfp.rfp_id)}`}
                className="mt-1 inline-block text-xs font-semibold text-zo-teal hover:text-zo-orange"
              >
                Open RFP →
              </Link>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
              <span className={CHIP}>{rfp.run_count} runs · {formatCost(rfp.cost_usd)}</span>
              <span className={CHIP}>{rfp.tweak_count} tweaks · {rfp.sections_touched} sections</span>
              <span className={CHIP}>{formatWordDelta(rfp.words_added, rfp.words_removed)} words</span>
              <span className={CHIP}>
                {rfp.manual_count} manual · {rfp.ai_count} AI-assisted · {rfp.other_count} other
              </span>
              {rfp.restore_count > 0 ? (
                <span className={CHIP}>{rfp.restore_count} restores/resets</span>
              ) : null}
            </div>
          </div>

          <div>
            <h4 className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">Viewed by</h4>
            {detail.viewers.length === 0 ? (
              <p className="mt-1 text-sm text-zo-text-muted">Nobody has opened this RFP since opens were recorded.</p>
            ) : (
              <ul className="mt-1 space-y-0.5 text-sm">
                {detail.viewers.map((v) => (
                  <li key={v.actor_email} className="flex gap-2">
                    <span className="font-semibold text-foreground">{v.actor_email}</span>
                    <span className="text-zo-text-muted">
                      {v.open_count} {v.open_count === 1 ? "visit" : "visits"} · last{" "}
                      {formatRelativeTime(v.last_opened_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">Timeline</h4>
              <label className="flex items-center gap-1.5 text-xs text-zo-text-secondary">
                <input
                  type="checkbox"
                  checked={showOpens}
                  onChange={(e) => setShowOpens(e.target.checked)}
                />
                Show opens
              </label>
            </div>
            {timeline.length === 0 ? (
              <p className="mt-2 text-sm text-zo-text-muted">No runs or tweaks yet.</p>
            ) : (
              <ul className="mt-2">
                {timeline.map((item) => (
                  <li
                    key={`${item.kind}-${item.at}-${item.kind === "run" ? item.run.run_id : item.kind === "tweak" ? item.tweak.id : item.actor}`}
                    className="flex gap-3 border-t border-black/5 py-3 first:border-t-0"
                  >
                    <time className="w-20 shrink-0 pt-0.5 text-[11px] tabular-nums text-zo-text-muted">
                      {formatRelativeTime(item.at)}
                    </time>
                    <div className="min-w-0 flex-1">
                      {item.kind === "run" ? (
                        <RunRow run={item.run} />
                      ) : item.kind === "tweak" ? (
                        <TweakRow tweak={item.tweak} />
                      ) : (
                        <p className="text-xs text-zo-text-muted">{item.actor} opened this RFP</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
