"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { listActiveProposalJobs, type ActiveProposalJob } from "@/lib/proposal-api";

const POLL_MS = 2000;
const POSITION_KEY = "zo-job-widget-position";
const WIDGET_WIDTH = 320;
const COLLAPSED_HEIGHT = 56;
const MARGIN = 16;
const DRAG_THRESHOLD_PX = 4;

type Position = { x: number; y: number };

// The /jobs/active endpoint only ever lists what's running right now — there
// is no "it just finished" event. A tracked job that drops out of that list
// between polls is inferred to have finished, and is kept (not discarded) so
// the widget can show a lasting confirmation instead of just vanishing.
type JobState = "running" | "finished";
type TrackedJob = ActiveProposalJob & { state: JobState };

function jobKey(job: ActiveProposalJob): string {
  return `${job.rfpId}:${job.jobType}`;
}

// Dismissal is per (job, state) — dismissing a running job hides it only
// while it stays running; the moment it finishes, that's new information
// worth surfacing again. Dismissing a finished job hides it until the same
// rfpId+jobType starts a genuinely new run, which arrives back in "running"
// state — a different composite key, so the old dismissal doesn't suppress it.
function dismissKey(job: TrackedJob): string {
  return `${jobKey(job)}:${job.state}`;
}

// Go/No-Go runs on an RFP that hasn't been decided "Go" yet, so it isn't in
// the Proposals workspace's RFP list at all — sending it to /proposals?rfp=
// lands on whatever RFP was already selected there instead (silently, since
// that page just ignores an id it doesn't recognize). The RFP's own detail
// page is where Go/No-Go actually lives; every other job type is a proposal
// pipeline phase, which does belong in the workspace.
function jobHref(job: ActiveProposalJob): string {
  return job.jobType === "go-no-go"
    ? `/rfps/${encodeURIComponent(job.rfpId)}`
    : `/proposals?rfp=${encodeURIComponent(job.rfpId)}`;
}

function clampX(x: number): number {
  if (typeof window === "undefined") return x;
  const maxX = Math.max(MARGIN, window.innerWidth - WIDGET_WIDTH - MARGIN);
  return Math.min(Math.max(x, MARGIN), maxX);
}

// position.y always anchors the COLLAPSED pill, regardless of expanded
// state — expanding grows the card upward from that fixed anchor (see
// renderTop below) instead of moving it, so it never needs re-clamping
// just because the job list got taller.
function clampY(y: number): number {
  if (typeof window === "undefined") return y;
  const maxY = Math.max(MARGIN, window.innerHeight - COLLAPSED_HEIGHT - MARGIN);
  return Math.min(Math.max(y, MARGIN), maxY);
}

function defaultPosition(): Position {
  if (typeof window === "undefined") return { x: MARGIN, y: MARGIN };
  return {
    x: clampX(window.innerWidth - WIDGET_WIDTH - MARGIN),
    y: clampY(window.innerHeight - COLLAPSED_HEIGHT - MARGIN),
  };
}

function RunningDot() {
  return (
    <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden>
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-500" />
    </span>
  );
}

function FinishedCheck() {
  return (
    <span
      className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700"
      aria-hidden
    >
      <svg className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M20 6L9 17l-5-5" />
      </svg>
    </span>
  );
}

/** Shows whenever a proposal pipeline or Go/No-Go analysis is running for any
 * RFP — polls a global, cross-RFP endpoint (not scoped to whichever
 * workspace happens to be mounted), so it keeps showing no matter which page
 * the user navigates to. When a run finishes it stays visible with a
 * checkmark instead of disappearing, so there's a lasting confirmation it
 * actually completed — dismissed manually, not on a timer. Floating,
 * draggable, and dismissible so it never blocks work — see PRODUCT.md: plain
 * language, generous targets, nothing the user can't easily get out of the
 * way. */
export function GlobalJobStatusWidget() {
  const router = useRouter();
  const [tracked, setTracked] = useState<Map<string, TrackedJob>>(new Map());
  const [dismissedKeys, setDismissedKeys] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState(false);
  const [position, setPosition] = useState<Position | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragStateRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const active = await listActiveProposalJobs({ includeGoNoGo: true });
      if (cancelled) return;
      setTracked((prev) => {
        const activeKeys = new Set(active.map(jobKey));
        const next = new Map(prev);
        // A previously-running job no longer in the active list has finished
        // — keep its last-known data (title/label) instead of dropping it.
        for (const [key, job] of prev) {
          if (job.state === "running" && !activeKeys.has(key)) {
            next.set(key, { ...job, state: "finished" });
          }
        }
        // Fresh data always wins for whatever's running right now.
        for (const job of active) {
          next.set(jobKey(job), { ...job, state: "running" });
        }
        return next;
      });
    };
    void poll();

    // 2s is fast enough to feel immediate, but this now runs on every
    // authenticated page for every open tab — pausing while the tab is
    // backgrounded keeps that snappy without polling for nobody. Polling
    // resumes (and catches up right away) the moment the tab is visible
    // again, so switching back never shows stale state.
    let id: number | null = null;
    const start = () => {
      if (id === null) id = window.setInterval(poll, POLL_MS);
    };
    const stop = () => {
      if (id !== null) {
        window.clearInterval(id);
        id = null;
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void poll();
        start();
      } else {
        stop();
      }
    };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  const visibleJobs = Array.from(tracked.values())
    .filter((j) => !dismissedKeys.has(dismissKey(j)))
    // Still-running work first — it's the more time-sensitive of the two.
    .sort((a, b) => (a.state === b.state ? 0 : a.state === "running" ? -1 : 1));
  // One job needs no disambiguation — the header already says everything
  // there is to say, so there's nothing left to reveal by expanding it.
  const canExpand = visibleJobs.length > 1;
  const isExpanded = expanded && canExpand;

  // Read fresh inside the window-level pointerup handler below, which is
  // only re-attached when its own deps change — visibleJobs is not one of
  // them (it changes on every poll tick), so a plain closure over it would
  // go stale between polls. Synced in an effect (post-render), not written
  // during render itself.
  const visibleJobsRef = useRef(visibleJobs);
  useEffect(() => {
    visibleJobsRef.current = visibleJobs;
  });

  const maxExpandedHeight =
    typeof window !== "undefined" ? window.innerHeight - MARGIN * 2 : 400;
  const height = isExpanded
    ? Math.min(56 + visibleJobs.length * 64 + 48, maxExpandedHeight)
    : COLLAPSED_HEIGHT;

  useEffect(() => {
    if (position) return;
    try {
      const raw = window.localStorage.getItem(POSITION_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Position;
        if (typeof parsed.x === "number" && typeof parsed.y === "number") {
          setPosition({ x: clampX(parsed.x), y: clampY(parsed.y) });
          return;
        }
      }
    } catch {
      /* ignore */
    }
    setPosition(defaultPosition());
    // Mount-only: the `if (position) return` above is the actual guard —
    // re-running this on every position change (i.e. every drag-move tick)
    // would just be a wasted no-op check on each one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onResize = () =>
      setPosition((p) => (p ? { x: clampX(p.x), y: clampY(p.y) } : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const persistPosition = useCallback((pos: Position) => {
    try {
      window.localStorage.setItem(POSITION_KEY, JSON.stringify(pos));
    } catch {
      /* ignore */
    }
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!position) return;
      dragStateRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        originX: position.x,
        originY: position.y,
        moved: false,
      };
    },
    [position]
  );

  const activatePrimary = useCallback(() => {
    const current = visibleJobsRef.current;
    if (current.length === 1) {
      router.push(jobHref(current[0]));
    } else if (current.length > 1) {
      setExpanded((v) => !v);
    }
  }, [router]);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      if (!drag.moved) {
        drag.moved = true;
        setIsDragging(true);
      }
      setPosition({ x: clampX(drag.originX + dx), y: clampY(drag.originY + dy) });
    };
    const onUp = (e: PointerEvent) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      dragStateRef.current = null;
      setIsDragging(false);
      if (drag.moved) {
        setPosition((p) => {
          if (p) persistPosition(p);
          return p;
        });
      } else {
        activatePrimary();
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [persistPosition, activatePrimary]);

  if (visibleJobs.length === 0 || !position) return null;

  const primary = visibleJobs[0];
  const runningCount = visibleJobs.filter((j) => j.state === "running").length;
  const finishedCount = visibleJobs.length - runningCount;
  const summary =
    visibleJobs.length === 1
      ? primary.state === "finished"
        ? `${primary.jobLabel} finished`
        : primary.jobLabel
      : [
          runningCount > 0 ? `${runningCount} running` : null,
          finishedCount > 0 ? `${finishedCount} finished` : null,
        ]
          .filter(Boolean)
          .join(", ");
  const subtitle = visibleJobs.length === 1 ? primary.title : null;

  const dismissAll = () => {
    const finishedIds = visibleJobs
      .filter((j) => j.state === "finished")
      .map((j) => jobKey(j));
    setDismissedKeys((prev) => {
      const next = new Set(prev);
      visibleJobs.forEach((j) => next.add(dismissKey(j)));
      return next;
    });
    // Finished + dismissed has nothing left to ever come back to unless a
    // fresh run starts, which re-adds it as "running" on the next poll
    // anyway — so there's no reason to keep the stale finished record
    // around in the meantime.
    if (finishedIds.length > 0) {
      setTracked((prev) => {
        const next = new Map(prev);
        finishedIds.forEach((k) => next.delete(k));
        return next;
      });
    }
    setExpanded(false);
  };

  // Anchored to the collapsed pill's bottom edge — expanding grows upward,
  // never downward off the bottom of the screen where this defaults to.
  const renderTop = Math.max(MARGIN, position.y - (height - COLLAPSED_HEIGHT));

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        left: position.x,
        top: renderTop,
        width: WIDGET_WIDTH,
        zIndex: 200,
      }}
      className="select-none"
    >
      <div
        className={`overflow-hidden rounded-2xl border border-zo-border/80 bg-white shadow-[0_12px_32px_rgba(10,15,26,0.18)] transition-shadow ${
          isDragging ? "shadow-[0_18px_44px_rgba(10,15,26,0.26)]" : ""
        }`}
      >
        <div
          onPointerDown={onPointerDown}
          role={canExpand ? undefined : "button"}
          tabIndex={canExpand ? undefined : 0}
          title={canExpand ? undefined : `Open ${primary.title}`}
          onKeyDown={
            canExpand
              ? undefined
              : (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    activatePrimary();
                  }
                }
          }
          className={`flex items-center gap-2.5 px-3.5 py-3 ${
            isDragging ? "cursor-grabbing" : "cursor-grab"
          } ${canExpand ? "" : "focus:outline-none focus-visible:ring-2 focus-visible:ring-[#ef5018]/30 focus-visible:ring-inset"}`}
        >
          {(visibleJobs.length === 1 ? primary.state === "running" : runningCount > 0) ? (
            <RunningDot />
          ) : (
            <FinishedCheck />
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold text-foreground">
              {summary}
            </p>
            {subtitle ? (
              <p className="truncate text-[11px] text-zo-text-muted">{subtitle}</p>
            ) : null}
          </div>
          {canExpand ? (
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => setExpanded((v) => !v)}
              aria-label={isExpanded ? "Show less" : "Show details"}
              aria-expanded={isExpanded}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-zo-text-secondary transition-smooth hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#ef5018]/30"
            >
              <svg
                className={`h-3.5 w-3.5 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
              </svg>
            </button>
          ) : null}
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={dismissAll}
            aria-label="Hide this"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-zo-text-secondary transition-smooth hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#ef5018]/30"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {isExpanded ? (
          <div className="max-h-[50vh] overflow-y-auto border-t border-zo-border/60 custom-scrollbar">
            {visibleJobs.map((job) => (
              <button
                key={dismissKey(job)}
                type="button"
                onClick={() => {
                  router.push(jobHref(job));
                  setExpanded(false);
                }}
                className="flex w-full items-center gap-2.5 border-b border-zo-border/40 px-3.5 py-2.5 text-left transition-smooth last:border-b-0 hover:bg-[#fafbfc] focus:outline-none focus-visible:bg-[#fafbfc]"
              >
                {job.state === "running" ? <RunningDot /> : <FinishedCheck />}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[12.5px] font-semibold text-foreground">
                    {job.title}
                  </p>
                  <p className="truncate text-[11px] text-zo-text-muted">
                    {job.state === "finished" ? `${job.jobLabel} — finished` : job.jobLabel}
                  </p>
                </div>
                <svg className="h-3.5 w-3.5 shrink-0 text-zo-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
