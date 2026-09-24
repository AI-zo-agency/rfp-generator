"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchAnalyticsSummary,
  formatDuration,
  type AnalyticsSummary,
} from "@/lib/user-analytics-api";
import type { AnalyticsWorkspace } from "@/lib/zo-analytics";

type Tone = "rfp" | "financial" | "leads";

const TONE: Record<Tone, { accent: string; chip: string; bar: string }> = {
  rfp: {
    accent: "text-[#ef5018]",
    chip: "bg-black/[0.05] text-zo-text-secondary",
    bar: "bg-[#ef5018]",
  },
  financial: {
    accent: "text-[#3C5A56]",
    chip: "bg-[#3C5A56]/10 text-[#274742]",
    bar: "bg-[#3C5A56]",
  },
  leads: {
    accent: "text-[#ef5018]",
    chip: "bg-[#274742]/8 text-[#274742]",
    bar: "bg-[#274742]",
  },
};

function defaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 14);
  return d.toISOString();
}

interface UserAnalyticsPanelProps {
  workspace: AnalyticsWorkspace;
  tone?: Tone;
  className?: string;
}

export function UserAnalyticsPanel({
  workspace,
  tone,
  className = "",
}: UserAnalyticsPanelProps) {
  const skin = TONE[tone ?? workspace];
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const summary = await fetchAnalyticsSummary({
        workspace,
        from: defaultFrom(),
        actor: actor || undefined,
      });
      setData(summary);
      if (summary.error) setError(summary.error);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [workspace, actor]);

  useEffect(() => {
    void load();
  }, [load]);

  const maxClicks = Math.max(
    1,
    ...(data?.top_features.map((f) => f.clicks) ?? [1]),
  );

  return (
    <section
      className={`flex min-h-0 flex-col rounded-2xl border border-black/5 bg-white/80 p-5 sm:p-6 ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground">
            Product analytics
          </h2>
          <p className="mt-1 max-w-xl text-xs text-zo-text-muted">
            Visible + engaged time, feature clicks, and funnels across all users
            (last 14 days). This Activity/Analytics surface is excluded from
            tracking. Dwell pauses when the browser tab is hidden.
          </p>
        </div>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void load();
          }}
        >
          <input
            type="search"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            placeholder="Filter by email"
            className="min-w-[180px] rounded-lg border border-black/10 px-3 py-2 text-sm outline-none focus:border-black/30"
          />
          <button
            type="submit"
            className={`rounded-lg px-3 py-2 text-xs font-bold text-white ${skin.bar}`}
          >
            Refresh
          </button>
        </form>
      </div>

      {loading ? (
        <p className="mt-8 text-sm text-zo-text-muted animate-pulse">
          Loading analytics…
        </p>
      ) : error && !data ? (
        <p className="mt-8 text-sm text-red-600">{error}</p>
      ) : (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Visible time"
              value={formatDuration(data?.time.visible_ms ?? 0)}
              chip={skin.chip}
            />
            <StatCard
              label="Engaged time"
              value={formatDuration(data?.time.engaged_ms ?? 0)}
              chip={skin.chip}
            />
            <StatCard
              label="Feature clicks"
              value={String(
                data?.top_features.reduce((a, f) => a + f.clicks, 0) ?? 0,
              )}
              chip={skin.chip}
            />
            <StatCard
              label="Active users"
              value={String(data?.by_user.length ?? 0)}
              chip={skin.chip}
            />
          </div>

          <div className="mt-8 grid gap-8 lg:grid-cols-2">
            <div>
              <h3 className={`text-sm font-semibold ${skin.accent}`}>
                Most-used features
              </h3>
              {(data?.top_features.length ?? 0) === 0 ? (
                <p className="mt-3 text-sm text-zo-text-muted">
                  No feature clicks yet — use the workspace and refresh.
                </p>
              ) : (
                <ol className="mt-3 space-y-2">
                  {data!.top_features.map((f, i) => (
                    <li
                      key={f.feature}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="min-w-0 truncate">
                        <span className="mr-2 text-zo-text-muted">{i + 1}.</span>
                        {f.feature}
                      </span>
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${skin.chip}`}>
                        {f.clicks} · {f.unique_users} users
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>

            <div>
              <h3 className={`text-sm font-semibold ${skin.accent}`}>
                Feature-wise graph
              </h3>
              {(data?.top_features.length ?? 0) === 0 ? (
                <p className="mt-3 text-sm text-zo-text-muted">No data yet.</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {data!.top_features.slice(0, 12).map((f) => (
                    <li key={f.feature}>
                      <div className="mb-1 flex justify-between gap-2 text-xs">
                        <span className="truncate font-medium">{f.feature}</span>
                        <span className="tabular-nums text-zo-text-muted">
                          {f.clicks}
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-black/[0.06]">
                        <div
                          className={`h-full rounded-full ${skin.bar}`}
                          style={{
                            width: `${Math.max(4, (f.clicks / maxClicks) * 100)}%`,
                          }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="mt-8 grid gap-8 lg:grid-cols-2">
            <div>
              <h3 className={`text-sm font-semibold ${skin.accent}`}>
                Top pages / tabs
              </h3>
              {(data?.top_pages.length ?? 0) === 0 ? (
                <p className="mt-3 text-sm text-zo-text-muted">No pageviews yet.</p>
              ) : (
                <ul className="mt-3 space-y-2 text-sm">
                  {data!.top_pages.map((p) => (
                    <li
                      key={p.path}
                      className="flex justify-between gap-2 border-b border-black/5 py-1.5"
                    >
                      <span className="truncate">{p.path}</span>
                      <span className="tabular-nums text-zo-text-muted">
                        {p.views}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h3 className={`text-sm font-semibold ${skin.accent}`}>Funnels</h3>
              {(data?.funnels.length ?? 0) === 0 ? (
                <p className="mt-3 text-sm text-zo-text-muted">No funnel data.</p>
              ) : (
                <div className="mt-3 space-y-5">
                  {data!.funnels.map((funnel) => {
                    const maxU = Math.max(
                      1,
                      ...funnel.steps.map((s) => s.users),
                    );
                    return (
                      <div key={funnel.id}>
                        <p className="text-xs font-semibold text-zo-text-secondary">
                          {funnel.label}
                        </p>
                        <ul className="mt-2 space-y-2">
                          {funnel.steps.map((step) => (
                            <li key={step.id}>
                              <div className="mb-0.5 flex justify-between text-[11px]">
                                <span>{step.id}</span>
                                <span>{step.users} users</span>
                              </div>
                              <div className="h-1.5 overflow-hidden rounded-full bg-black/[0.06]">
                                <div
                                  className={`h-full rounded-full ${skin.bar}`}
                                  style={{
                                    width: `${Math.max(3, (step.users / maxU) * 100)}%`,
                                  }}
                                />
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          <div className="mt-8">
            <h3 className={`text-sm font-semibold ${skin.accent}`}>Per user</h3>
            {(data?.by_user.length ?? 0) === 0 ? (
              <p className="mt-3 text-sm text-zo-text-muted">No users yet.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[480px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-black/10 text-[11px] uppercase tracking-wide text-zo-text-muted">
                      <th className="py-2 pr-3 font-semibold">User</th>
                      <th className="py-2 pr-3 font-semibold">Visible</th>
                      <th className="py-2 pr-3 font-semibold">Engaged</th>
                      <th className="py-2 pr-3 font-semibold">Views</th>
                      <th className="py-2 font-semibold">Clicks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data!.by_user.map((u) => (
                      <tr
                        key={u.actor_email}
                        className="border-b border-black/5"
                      >
                        <td className="py-2 pr-3">{u.actor_email}</td>
                        <td className="py-2 pr-3 tabular-nums">
                          {formatDuration(u.visible_ms)}
                        </td>
                        <td className="py-2 pr-3 tabular-nums">
                          {formatDuration(u.engaged_ms)}
                        </td>
                        <td className="py-2 pr-3 tabular-nums">{u.page_views}</td>
                        <td className="py-2 tabular-nums">{u.clicks}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function StatCard({
  label,
  value,
  chip,
}: {
  label: string;
  value: string;
  chip: string;
}) {
  return (
    <div className={`rounded-xl px-4 py-3 ${chip}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wide opacity-70">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}
