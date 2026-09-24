"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatRelativeTime } from "@/lib/format";
import {
  fetchUserActivity,
  hrefForActivityEvent,
  type ActivityWorkspace,
  type UserActivityEvent,
  type UserActivityStats,
} from "@/lib/user-activity-api";

type Tone = "rfp" | "financial" | "leads";

const TONE_STYLES: Record<
  Tone,
  { accent: string; chip: string; button: string; input: string }
> = {
  rfp: {
    accent: "bg-[#ef5018]",
    chip: "bg-black/[0.05] text-zo-text-secondary",
    button: "bg-[#ef5018] text-white hover:opacity-90",
    input: "border-zo-border focus:border-[#ef5018]",
  },
  financial: {
    accent: "bg-[#3C5A56]",
    chip: "bg-[#3C5A56]/10 text-[#274742]",
    button: "bg-[#3C5A56] text-white hover:opacity-90",
    input: "border-[#274742]/20 focus:border-[#3C5A56]",
  },
  leads: {
    accent: "bg-[#ef5018]",
    chip: "bg-[#274742]/8 text-[#274742]",
    button: "bg-[#274742] text-white hover:opacity-90",
    input: "border-[#274742]/20 focus:border-[#ef5018]",
  },
};

interface UserActivityPanelProps {
  workspace: ActivityWorkspace;
  tone?: Tone;
  title?: string;
  subtitle?: string;
  className?: string;
}

export function UserActivityPanel({
  workspace,
  tone,
  title = "User Activity",
  subtitle = "Who did what in this workspace",
  className = "",
}: UserActivityPanelProps) {
  const skin = TONE_STYLES[tone ?? workspace];
  const [items, setItems] = useState<UserActivityEvent[]>([]);
  const [stats, setStats] = useState<UserActivityStats>({
    events_today: 0,
    unique_actors_7d: 0,
  });
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [actorFilter, setActorFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (opts?: { cursor?: string | null; append?: boolean }) => {
      const append = Boolean(opts?.append);
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      try {
        const data = await fetchUserActivity({
          workspace,
          limit: 40,
          cursor: opts?.cursor,
          actor: actorFilter || undefined,
        });
        setStats(data.stats || { events_today: 0, unique_actors_7d: 0 });
        setNextCursor(data.next_cursor);
        setItems((prev) => (append ? [...prev, ...data.items] : data.items));
        if (data.error) setError(data.error);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load activity");
        if (!append) setItems([]);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [workspace, actorFilter],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section
      className={`flex h-full min-h-0 flex-col rounded-2xl border border-black/5 bg-white/80 p-5 sm:p-6 ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground">{title}</h2>
          <p className="mt-1 text-xs text-zo-text-muted">{subtitle}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px]">
          <span className={`rounded-md px-2 py-1 font-semibold tabular-nums ${skin.chip}`}>
            {stats.events_today} today
          </span>
          <span className={`rounded-md px-2 py-1 font-semibold tabular-nums ${skin.chip}`}>
            {stats.unique_actors_7d} actors · 7d
          </span>
        </div>
      </div>

      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <input
          type="search"
          value={actorFilter}
          onChange={(e) => setActorFilter(e.target.value)}
          placeholder="Filter by email"
          className={`min-w-[200px] flex-1 rounded-lg border bg-white px-3 py-2 text-sm outline-none ${skin.input}`}
        />
        <button
          type="submit"
          className={`rounded-lg px-3 py-2 text-xs font-bold transition ${skin.button}`}
        >
          Apply
        </button>
        <button
          type="button"
          onClick={() => {
            setActorFilter("");
            void load();
          }}
          className="rounded-lg border border-black/10 px-3 py-2 text-xs font-semibold text-zo-text-secondary hover:bg-black/[0.03]"
        >
          Refresh
        </button>
      </form>

      {loading ? (
        <p className="mt-8 text-sm text-zo-text-muted animate-pulse">Loading activity…</p>
      ) : error ? (
        <p className="mt-8 text-sm text-red-600">{error}</p>
      ) : items.length === 0 ? (
        <p className="mt-8 text-sm text-zo-text-muted">
          No activity yet — actions in this workspace will show up here.
        </p>
      ) : (
        <ul className="mt-4 min-h-0 flex-1 overflow-y-auto">
          {items.map((item) => {
            const href = hrefForActivityEvent(item);
            const inner = (
              <>
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${skin.accent}`}
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-semibold leading-snug text-foreground">
                      {item.summary}
                    </p>
                    <time className="shrink-0 pt-0.5 text-[11px] tabular-nums text-zo-text-muted">
                      {formatRelativeTime(item.created_at)}
                    </time>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zo-text-muted">
                    <span>{item.actor_email || "unknown"}</span>
                    {item.entity_label ? (
                      <>
                        <span aria-hidden>·</span>
                        <span className="truncate">{item.entity_label}</span>
                      </>
                    ) : null}
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${skin.chip}`}
                    >
                      {item.action}
                    </span>
                    {item.outcome && item.outcome !== "recorded" ? (
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${skin.chip}`}
                      >
                        {item.outcome}
                      </span>
                    ) : null}
                  </div>
                </div>
              </>
            );
            return (
              <li key={item.id} className="border-t border-black/5 first:border-t-0">
                {href ? (
                  <Link
                    href={href}
                    className="group flex gap-3 py-3.5 transition-colors hover:bg-black/[0.02]"
                  >
                    {inner}
                  </Link>
                ) : (
                  <div className="flex gap-3 py-3.5">{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {nextCursor ? (
        <button
          type="button"
          disabled={loadingMore}
          onClick={() => void load({ cursor: nextCursor, append: true })}
          className="mt-4 self-center rounded-lg border border-black/10 px-4 py-2 text-xs font-semibold text-zo-text-secondary hover:bg-black/[0.03] disabled:opacity-50"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </section>
  );
}
