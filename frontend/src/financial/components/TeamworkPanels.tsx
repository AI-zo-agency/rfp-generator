"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { RefreshCw } from "lucide-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DataTable, Figure, Note, Panel } from "./qb-ui";
import type {
  TeamworkMilestone,
  TeamworkOverview,
  TeamworkPerson,
  TeamworkProject,
  TeamworkTask,
  TeamworkTimeBucket,
} from "../types/teamwork";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

function isAbortError(err: unknown) {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

function shortDate(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
  return parsed.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function hoursLabel(minutes: number) {
  const hours = minutes / 60;
  if (!hours) return "0h";
  return `${hours.toFixed(hours >= 10 ? 0 : 1)}h`;
}

function healthLabel(health: string) {
  if (health === "bad") return "At risk";
  if (health === "ok") return "OK";
  if (health === "good") return "Good";
  return "—";
}

export function TeamworkPanels() {
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

  const projectCols = useMemo<ColumnDef<TeamworkProject, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Project",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "company_name", header: "Client" },
      { accessorKey: "status", header: "Status" },
      {
        accessorKey: "health",
        header: "Health",
        cell: (c) => healthLabel(c.getValue<string>() ?? ""),
      },
      {
        accessorKey: "progress_pct",
        header: "Done",
        meta: { numeric: true },
        cell: (c) => `${c.getValue<number>()}%`,
      },
      {
        accessorKey: "tasks_overdue",
        header: "Overdue",
        meta: { numeric: true },
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => shortDate(c.getValue<string | null>()),
      },
    ],
    [],
  );

  const taskCols = useMemo<ColumnDef<TeamworkTask, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Task",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "project_name", header: "Project" },
      {
        accessorKey: "assignees",
        header: "Assigned",
        cell: (c) => (c.getValue<string[]>() || []).join(", ") || "—",
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => shortDate(c.getValue<string | null>()),
      },
    ],
    [],
  );

  const milestoneCols = useMemo<ColumnDef<TeamworkMilestone, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Milestone",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "project_name", header: "Project" },
      { accessorKey: "status", header: "Status" },
      {
        accessorKey: "progress_pct",
        header: "Done",
        meta: { numeric: true },
        cell: (c) => (c.getValue<number | null>() == null ? "—" : `${c.getValue<number>()}%`),
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => shortDate(c.getValue<string | null>()),
      },
    ],
    [],
  );

  const timeCols = useMemo<ColumnDef<TeamworkTimeBucket, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "minutes",
        header: "Hours",
        meta: { numeric: true },
        cell: (c) => hoursLabel(c.getValue<number>()),
      },
    ],
    [],
  );

  const peopleCols = useMemo<ColumnDef<TeamworkPerson, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Person",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "title", header: "Title", cell: (c) => c.getValue<string | null>() || "—" },
      { accessorKey: "email", header: "Email" },
    ],
    [],
  );

  const errorEntries = Object.entries(data?.errors || {});
  const notConfigured = data && !data.connected && Boolean(data.errors.config || data.errors.auth);
  let syncLabel = "Cached";
  if (loading) syncLabel = "Reading Teamwork…";
  else if (error) syncLabel = "Unavailable";
  else if (notConfigured) syncLabel = "Not connected";
  else if (data?.sync_status === "backfill_pending") syncLabel = "Backfill pending";
  else if (data?.sync_status === "missing") syncLabel = "Snapshot missing";
  else if (data?.sync_status === "failed") syncLabel = "Stale cache";
  else if (errorEntries.length) syncLabel = "Partial";

  return (
    <TooltipProvider delayDuration={120}>
      <div className="qb-ledger" aria-busy={loading || undefined}>
        <div className="qb-toolbar">
          <p className="qb-sync" data-failed={error || notConfigured ? "true" : undefined}>
            <span className="qb-sync-dot" data-busy={loading ? "true" : undefined} aria-hidden />
            {syncLabel}
            {!loading && (data?.synced_at || data?.generated_at) ? (
              <span className="qb-sync-meta">
                {new Date(data.synced_at || data.generated_at || "").toLocaleString()}
              </span>
            ) : null}
            {!loading && data?.summary ? (
              <span className="qb-sync-meta">{data.summary.project_count} active projects</span>
            ) : null}
          </p>
          <button
            type="button"
            className="qb-retry"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>

        {loading && !data ? (
          <p className="qb-empty">Loading Teamwork projects…</p>
        ) : null}

        {!loading && (error || !data) ? (
          <div className="qb-error">
            <p>{error ?? "No Teamwork data"}</p>
            <button type="button" className="qb-retry" onClick={() => void load()}>
              Try again
            </button>
          </div>
        ) : null}

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
        ) : null}

        {!loading && data && !notConfigured ? (
          <div className="min-h-0 flex-1 overflow-auto" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div className="qb-moneyline">
              <Figure
                label="Active projects"
                size="lg"
                value={data.summary.project_count}
                sub="Current, late, and upcoming"
              />
              <Figure
                label="Overdue tasks"
                size="lg"
                value={data.summary.overdue_task_count}
                tone={data.summary.overdue_task_count ? "out" : undefined}
                sub="Across all accessible projects"
              />
              <Figure
                label="Hours this month"
                size="lg"
                value={`${data.summary.hours_this_month}h`}
                sub={`${hoursLabel(data.time.billable_minutes)} billable`}
              />
              <Figure
                label="Late milestones"
                size="lg"
                value={data.summary.late_milestone_count}
                tone={data.summary.late_milestone_count ? "warn" : undefined}
                sub={`${data.summary.upcoming_task_count} tasks due in 14 days`}
              />
            </div>

            {errorEntries.length ? (
              <Note>
                Teamwork sync has issues ({errorEntries.map(([key]) => key).join(", ")}). Showing the
                last cached snapshot from our backend mirror.
              </Note>
            ) : null}

            <Panel title="Projects" meta={`${data.projects.length} shown`}>
              <DataTable
                data={data.projects}
                columns={projectCols}
                initialSort="tasks_overdue"
                pageSize={12}
                empty="No active Teamwork projects for this API user."
              />
            </Panel>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel title="Overdue tasks" meta={`${data.overdue_tasks.length}`}>
                <DataTable
                  data={data.overdue_tasks}
                  columns={taskCols}
                  pageSize={8}
                  empty="Nothing overdue."
                />
              </Panel>
              <Panel title="Due in the next 14 days" meta={`${data.upcoming_tasks.length}`}>
                <DataTable
                  data={data.upcoming_tasks}
                  columns={taskCols}
                  pageSize={8}
                  empty="No upcoming due dates in the next two weeks."
                />
              </Panel>
            </div>

            <Panel title="Milestones" meta={`${data.milestones.length}`}>
              <DataTable
                data={data.milestones}
                columns={milestoneCols}
                pageSize={8}
                empty="No late or upcoming milestones."
              />
            </Panel>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel title="Time by person" meta={hoursLabel(data.time.total_minutes)}>
                <DataTable
                  data={data.time.by_person}
                  columns={timeCols}
                  initialSort="minutes"
                  pageSize={8}
                  empty="No time logged this month."
                />
              </Panel>
              <Panel title="Time by project" meta={hoursLabel(data.time.total_minutes)}>
                <DataTable
                  data={data.time.by_project}
                  columns={timeCols}
                  initialSort="minutes"
                  pageSize={8}
                  empty="No time logged this month."
                />
              </Panel>
            </div>

            <Panel title="People" meta={`${data.people.length} owner-company users`}>
              <DataTable
                data={data.people}
                columns={peopleCols}
                pageSize={12}
                empty="No owner-company people returned."
              />
            </Panel>
          </div>
        ) : null}
      </div>
    </TooltipProvider>
  );
}
