"use client";

import Link from "next/link";
import { formatRelativeTime } from "@/lib/format";
import {
  summarizeActivityAction,
  type ActivityKind,
} from "@/lib/dashboard-activity";
import type { ActivityItem } from "@/types/rfp";

interface ActivityFeedProps {
  items: ActivityItem[];
}

function kindTone(kind: ActivityKind): string {
  switch (kind) {
    case "proposal":
      return "bg-[#ef5018]";
    case "go_no_go":
      return "bg-zo-teal";
    case "personas":
      return "bg-[#ef5018]/70";
    default:
      return "bg-zo-text-muted";
  }
}

function hrefFor(item: ActivityItem, kind: ActivityKind): string {
  if (kind === "proposal" || kind === "personas" || item.actor === "Proposal") {
    return `/proposals?rfp=${encodeURIComponent(item.rfpId)}`;
  }
  return `/rfps/${encodeURIComponent(item.rfpId)}`;
}

export function ActivityFeed({ items }: ActivityFeedProps) {
  return (
    <section className="zo-card flex h-full flex-col p-6 sm:p-7">
      <h2 className="font-heading text-lg font-semibold text-foreground">
        Live Activity
      </h2>

      {items.length === 0 ? (
        <p className="mt-6 text-sm text-zo-text-muted">
          Nothing new yet — activity shows up as you analyze and draft.
        </p>
      ) : (
        <ul className="mt-5 flex-1">
          {items.map((item) => {
            const summary = summarizeActivityAction(item.action, item.actor);
            const href = hrefFor(item, summary.kind);

            return (
              <li
                key={item.id}
                className="border-t border-zo-border/70 first:border-t-0"
              >
                <Link
                  href={href}
                  className="group flex gap-3 py-3.5 transition-colors hover:bg-[var(--zo-hover-bg)]"
                >
                  <span
                    className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${kindTone(summary.kind)}`}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm font-semibold leading-snug text-foreground group-hover:text-zo-orange">
                        {summary.headline}
                      </p>
                      <time className="shrink-0 pt-0.5 text-[11px] tabular-nums text-zo-text-muted">
                        {formatRelativeTime(item.timestamp)}
                      </time>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <p className="truncate text-xs text-zo-text-muted">
                        {item.rfpTitle}
                      </p>
                      {summary.scoreLabel ? (
                        <span className="rounded-md bg-black/[0.05] px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-zo-text-secondary">
                          {summary.scoreLabel}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
