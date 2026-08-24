"use client";

/**
 * Teamwork delivery.
 *
 * Reading order matches the ledger: the first screen states the position,
 * then what needs a decision, then the one trend worth a chart. Projects,
 * tasks, and time each live on their own tab so they do not compete for
 * the same glance.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Empty, Figure, Note, Panel } from "./qb-ui";
import {
  billablePct,
  budgetPortfolio,
  buildSignals,
  daysUntil,
  describeOverdueHeat,
  formatUsdFromCents,
  hoursChartRows,
  hoursLabel,
  type SectionId,
} from "../lib/teamwork-derive";
import { parseTeamworkView, type TeamworkViewId } from "../lib/financial-tab";
import { TeamworkAttention } from "./teamwork/TeamworkAttention";
import { TeamworkProjects } from "./teamwork/TeamworkProjects";
import { TeamworkWork } from "./teamwork/TeamworkWork";
import { TeamworkTime } from "./teamwork/TeamworkTime";
import { Count, HoursValue } from "./teamwork/kpis";
import type { TeamworkOverview } from "../types/teamwork";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

const VIEWS = [
  { id: "position", label: "Position" },
  { id: "projects", label: "Projects" },
  { id: "work", label: "Tasks" },
  { id: "time", label: "Time" },
] as const;

function isAbortError(err: unknown) {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

/** "4 hours ago" — the form a reader can act on. The timestamp goes in the title. */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

type SyncTone = "ok" | "warn" | "bad";

function syncState(
  data: TeamworkOverview | null,
  loading: boolean,
  error: string | null,
  notConfigured: boolean,
): { label: string; tone: SyncTone } {
  if (loading) return { label: "Reading Teamwork…", tone: "ok" };
  if (error) return { label: "Unavailable", tone: "bad" };
  if (notConfigured) return { label: "Not connected", tone: "bad" };
  if (data?.sync_status === "backfill_pending") return { label: "Backfill pending", tone: "warn" };
  if (data?.sync_status === "missing") return { label: "Snapshot missing", tone: "warn" };
  if (data?.sync_status === "failed") return { label: "Stale cache", tone: "warn" };
  if (Object.keys(data?.errors || {}).length) return { label: "Partial sync", tone: "warn" };
  return { label: "Synced", tone: "ok" };
}

function LedgerSkeleton() {
  return (
    <div className="qb-skel" aria-busy="true" aria-live="polite" aria-label="Reading Teamwork">
      <div className="qb-skel-block" style={{ height: 88 }} />
      <div className="qb-skel-block" style={{ height: 220 }} />
      <div className="qb-skel-block" style={{ height: 230 }} />
    </div>
  );
}

function DeliveryLine({
  data,
  atRiskCount,
  oldestLate,
}: {
  data: TeamworkOverview;
  atRiskCount: number;
  oldestLate: number;
}) {
  const overdueSub = describeOverdueHeat(data.overdue_tasks, oldestLate);
  const budget = budgetPortfolio(data.projects);
  const budgetSub =
    budget.projectCount > 0
      ? `${budget.budgetedCount} of ${budget.projectCount} projects budgeted`
      : "No active projects";
  const budgetValue =
    budget.capacity > 0 ? (
      <>
        {formatUsdFromCents(budget.used)}
        <span className="qb-figure-rest"> of {formatUsdFromCents(budget.capacity)}</span>
      </>
    ) : (
      "—"
    );

  return (
    <>
      <div className="qb-moneyline">
        <Figure
          label="Active projects"
          size="lg"
          metric="projects"
          value={<Count value={data.summary.project_count} />}
          sub={atRiskCount ? `${atRiskCount} at risk` : "None flagged at risk"}
        />
        <Figure
          label="Overdue tasks"
          size="lg"
          metric="overdue"
          value={<Count value={data.summary.overdue_task_count} />}
          tone={data.summary.overdue_task_count ? "out" : undefined}
          sub={overdueSub}
        />
        <Figure
          label="Due in 14 days"
          size="lg"
          metric="soon"
          value={<Count value={data.summary.upcoming_task_count} />}
          sub="Tasks with a near due date"
        />
        <Figure
          label="Hours this month"
          size="lg"
          metric="hours"
          value={<HoursValue minutes={data.time.total_minutes} />}
          sub={`${billablePct(data.time.billable_minutes, data.time.total_minutes)}% billable`}
        />
      </div>
      <div className="qb-moneyline">
        <Figure
          label="Budget in play"
          size="lg"
          metric="cash"
          value={budgetValue}
          sub={budgetSub}
        />
        <Figure
          label="Unbudgeted"
          size="lg"
          metric="flag"
          value={<Count value={budget.unbudgetedCount} />}
          tone={budget.unbudgetedCount > 0 ? "warn" : undefined}
          sub="Active projects with no Teamwork budget"
        />
      </div>
    </>
  );
}

function HoursChart({ data }: { data: TeamworkOverview }) {
  const { rows, split } = useMemo(() => hoursChartRows(data.time.by_person), [data.time.by_person]);
  const through = useMemo(() => {
    if (!data.time.period_end) return null;
    return new Date(`${data.time.period_end}T00:00:00Z`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }, [data.time.period_end]);
  const total = hoursLabel(data.time.total_minutes);
  const max = Math.max(...rows.map((row) => row.hours), 1);

  return (
    <Panel title="Hours this month" meta={through ? `${total} · Through ${through}` : total}>
      {rows.length ? (
        <>
          <div className="qb-legend">
            {split ? (
              <>
                <span>
                  <span className="qb-swatch" style={{ background: "var(--zo-teal)" }} aria-hidden />
                  Billable
                </span>
                <span>
                  <span className="qb-swatch" style={{ background: "var(--zo-orange)" }} aria-hidden />
                  Non-billable
                </span>
              </>
            ) : (
              <span>
                <span className="qb-swatch" style={{ background: "var(--zo-teal)" }} aria-hidden />
                Hours
              </span>
            )}
          </div>
          <div className="tw-hours" role="img" aria-label={`Hours this month, ${total}`}>
            {rows.map((row) => (
              <Tooltip key={row.name} delayDuration={0}>
                <TooltipTrigger asChild>
                  <button type="button" className="tw-hours__col">
                    <div className="tw-hours__track">
                      {split ? (
                        <>
                          <span
                            className="tw-hours__bar tw-hours__bar--billable"
                            style={{ height: `${(row.billable / max) * 100}%` }}
                          />
                          <span
                            className="tw-hours__bar tw-hours__bar--nb"
                            style={{ height: `${(row.nonBillable / max) * 100}%` }}
                          />
                        </>
                      ) : (
                        <span
                          className="tw-hours__bar tw-hours__bar--billable"
                          style={{ height: `${(row.hours / max) * 100}%` }}
                        />
                      )}
                    </div>
                    <span className="tw-hours__label">{row.name}</span>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8} className="qb-charttip [&_.rotate-45]:hidden">
                  <p className="qb-charttip-title">{row.name}</p>
                  {split ? (
                    <>
                      <p className="qb-charttip-row">
                        <span className="qb-swatch" style={{ background: "var(--zo-teal)" }} aria-hidden />
                        <span>Billable</span>
                        <strong>{row.billable}h</strong>
                      </p>
                      <p className="qb-charttip-row">
                        <span className="qb-swatch" style={{ background: "var(--zo-orange)" }} aria-hidden />
                        <span>Non-billable</span>
                        <strong>{row.nonBillable}h</strong>
                      </p>
                    </>
                  ) : (
                    <p className="qb-charttip-row">
                      <span className="qb-swatch" style={{ background: "var(--zo-teal)" }} aria-hidden />
                      <span>Hours</span>
                      <strong>{row.hours}h</strong>
                    </p>
                  )}
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </>
      ) : (
        <Empty>No time logged this month.</Empty>
      )}
    </Panel>
  );
}

export function TeamworkPanels({
  view,
  onViewChange,
}: {
  view: TeamworkViewId;
  onViewChange: (id: TeamworkViewId) => void;
}) {
  const [data, setData] = useState<TeamworkOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/financials/teamwork/overview`, {
        signal: ac.signal,
      });
      if (!res.ok) throw new Error(`Teamwork overview returned ${res.status}`);
      const payload = (await res.json()) as TeamworkOverview;
      if (ac.signal.aborted) return;
      setData(payload);
    } catch (err) {
      if (isAbortError(err) || ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Could not reach Teamwork");
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  const errorEntries = Object.entries(data?.errors || {});
  const notConfigured = data ? !data.connected : false;
  const hasSnapshot =
    Boolean(data) &&
    data?.sync_status !== "backfill_pending" &&
    data?.sync_status !== "missing" &&
    !data?.errors?.overview;
  const todayISO = useMemo(
    () => (data?.as_of || new Date().toISOString()).slice(0, 10),
    [data?.as_of],
  );
  const sync = syncState(data, loading, error, Boolean(notConfigured));
  const syncedAt = data?.synced_at || data?.generated_at || null;
  const atRiskCount = data?.projects.filter((p) => p.health === "bad").length ?? 0;
  const oldestLate = useMemo(() => {
    if (!data) return 0;
    let worst = 0;
    for (const t of data.overdue_tasks) {
      const days = daysUntil(t.due_date, todayISO);
      if (days !== null && days < 0) worst = Math.max(worst, Math.abs(days));
    }
    return worst;
  }, [data, todayISO]);

  const signals = useMemo(
    () => (data ? buildSignals(data, todayISO) : []),
    [data, todayISO],
  );

  const goToSection = useCallback(
    (id: SectionId) => {
      onViewChange(id);
    },
    [onViewChange],
  );

  const showBody = Boolean(data) && !notConfigured;
  const showError = !loading && !data && !notConfigured;
  const showSkeleton = loading && !data;

  return (
    <TooltipProvider delayDuration={120}>
      <div className="qb-ledger" aria-busy={loading || undefined}>
        <div className="qb-toolbar">
          <p className="qb-sync" data-tone={sync.tone}>
            <span className="qb-sync-dot" data-busy={loading ? "true" : undefined} aria-hidden />
            {sync.label}
            {!loading && syncedAt ? (
              <span className="qb-sync-meta" title={new Date(syncedAt).toLocaleString()}>
                {relativeTime(syncedAt)}
              </span>
            ) : null}
            {!loading && data?.summary ? (
              <span className="qb-sync-meta">
                {data.summary.project_count} active projects
              </span>
            ) : null}
          </p>
          <button type="button" className="qb-retry" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} strokeWidth={2.25} aria-hidden />
            Refresh
          </button>
        </div>

        {!loading && data && notConfigured ? (
          <div className="qb-error">
            <p>
              {data.errors.auth ||
                data.errors.config ||
                "Teamwork credentials are not configured on the backend."}
            </p>
            <Note>
              Set TEAMWORK_BASE_URL and TEAMWORK_API_KEY on the FastAPI service. The browser never
              receives the API key.
            </Note>
          </div>
        ) : (
          <Tabs
            value={view}
            onValueChange={(id) => onViewChange(parseTeamworkView(id))}
            className="qb-tabs"
          >
            <TabsList className="qb-tablist">
              {VIEWS.map((v) => (
                <TabsTrigger key={v.id} value={v.id}>
                  {v.label}
                </TabsTrigger>
              ))}
            </TabsList>

            {showSkeleton ? <LedgerSkeleton /> : null}

            {showError ? (
              <div className="qb-error">
                <p>{error ?? "No Teamwork data"}</p>
                <button type="button" className="qb-retry" onClick={() => void load()}>
                  <RefreshCw size={13} strokeWidth={2.25} aria-hidden /> Try again
                </button>
              </div>
            ) : null}

            {showBody && data ? (
              <>
                <TabsContent value="position" className="qb-view">
                  <DeliveryLine data={data} atRiskCount={atRiskCount} oldestLate={oldestLate} />
                  <TeamworkAttention
                    signals={signals}
                    onGo={goToSection}
                    hasSnapshot={hasSnapshot}
                  />
                  {errorEntries.length ? (
                    <Note>
                      Teamwork sync has issues ({errorEntries.map(([key]) => key).join(", ")}).{" "}
                      {hasSnapshot
                        ? "Showing the last cached snapshot from our backend mirror."
                        : "Teamwork has not completed a sync yet, so there is no snapshot to show."}
                    </Note>
                  ) : null}
                  <HoursChart data={data} />
                </TabsContent>

                <TabsContent value="projects" className="qb-view">
                  <TeamworkProjects data={data} todayISO={todayISO} />
                </TabsContent>

                <TabsContent value="work" className="qb-view">
                  <TeamworkWork data={data} todayISO={todayISO} />
                </TabsContent>

                <TabsContent value="time" className="qb-view">
                  <TeamworkTime data={data} />
                </TabsContent>
              </>
            ) : null}
          </Tabs>
        )}
      </div>
    </TooltipProvider>
  );
}
