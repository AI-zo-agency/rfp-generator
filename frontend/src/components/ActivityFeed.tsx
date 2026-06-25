import { formatRelativeTime } from "@/lib/format";
import type { ActivityItem } from "@/types/rfp";

interface ActivityFeedProps {
  items: ActivityItem[];
}

export function ActivityFeed({ items }: ActivityFeedProps) {
  return (
    <section className="zo-card p-8">
      <h2 className="font-heading text-lg font-bold text-foreground">
        Recent Activity
      </h2>
      <p className="mt-1 text-sm text-zo-text-muted">
        Latest updates across the pipeline
      </p>

      <ul className="mt-8 space-y-0">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex gap-5 border-b border-zo-border py-5 last:border-b-0"
          >
            <div className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-zo-teal" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-foreground">
                {item.action}
              </p>
              <p className="mt-1 truncate text-sm text-zo-text-secondary">
                {item.rfpTitle}
              </p>
              <p className="mt-2 text-xs text-zo-text-muted">
                {item.actor} · {formatRelativeTime(item.timestamp)}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
