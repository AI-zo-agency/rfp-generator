"use client";

import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, DueChip, Figure, MiniBar, Panel, Pill } from "../qb-ui";
import { daysUntil, describeDue, taskUrl } from "../../lib/teamwork-derive";
import type { TeamworkMilestone, TeamworkOverview, TeamworkTask } from "../../types/teamwork";
import { Count } from "./kpis";

/** Soonest-to-worst: the most negative day count is the most overdue. */
function byUrgency(tasks: TeamworkTask[], todayISO: string): TeamworkTask[] {
  return [...tasks].sort(
    (a, b) =>
      (daysUntil(a.due_date, todayISO) ?? Number.MAX_SAFE_INTEGER) -
      (daysUntil(b.due_date, todayISO) ?? Number.MAX_SAFE_INTEGER),
  );
}

export function TeamworkWork({
  data,
  todayISO,
}: {
  data: TeamworkOverview;
  todayISO: string;
}) {
  const overdue = useMemo(() => byUrgency(data.overdue_tasks, todayISO), [data.overdue_tasks, todayISO]);
  const upcoming = useMemo(() => byUrgency(data.upcoming_tasks, todayISO), [data.upcoming_tasks, todayISO]);
  const unassignedCount = overdue.filter((t) => t.assignees.length === 0).length;
  const lateMilestoneCount = data.milestones.filter(
    (m) => (m.status || "").toLowerCase() === "late",
  ).length;

  const taskCols = useMemo<ColumnDef<TeamworkTask, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Task",
        cell: (c) => {
          const task = c.row.original;
          const href = taskUrl(data.base_url, task.id);
          if (!href) return <span className="qb-name" title={task.name}>{task.name}</span>;
          return (
            <a className="qb-name" href={href} target="_blank" rel="noreferrer" title={task.name}>
              {task.name}
            </a>
          );
        },
      },
      {
        accessorKey: "project_name",
        header: "Project",
        cell: (c) => {
          const name = c.getValue<string>() || "";
          return <span className="qb-name" title={name}>{name}</span>;
        },
      },
      {
        accessorKey: "assignees",
        header: "Assigned",
        cell: (c) => {
          const assignees = c.getValue<string[]>() || [];
          // An owner-less overdue task cannot move on its own, so it gets a mark
          // rather than the em dash that used to make it look like missing data.
          if (!assignees.length) return <Pill label="Unassigned" tone="bad" />;
          const label = assignees.join(", ");
          return <span className="qb-name" title={label}>{label}</span>;
        },
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => {
          const due = describeDue(c.getValue<string | null>(), todayISO);
          return <DueChip label={due.label} tone={due.tone} />;
        },
      },
    ],
    [data.base_url, todayISO],
  );

  const milestoneCols = useMemo<ColumnDef<TeamworkMilestone, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Milestone",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "project_name", header: "Project" },
      {
        accessorKey: "status",
        header: "Status",
        cell: (c) => {
          const status = c.getValue<string>() || "";
          const isLate = status.toLowerCase() === "late";
          return <Pill label={status || "—"} tone={isLate ? "bad" : "neutral"} />;
        },
      },
      {
        accessorKey: "progress_pct",
        header: "Done",
        cell: (c) => {
          const pct = c.getValue<number | null>();
          if (pct == null) return <span className="qb-due" data-tone="none">—</span>;
          return <MiniBar value={pct} max={100} label={`${pct}%`} />;
        },
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => {
          const due = describeDue(c.getValue<string | null>(), todayISO);
          return <DueChip label={due.label} tone={due.tone} />;
        },
      },
    ],
    [todayISO],
  );

  return (
    <>
      <div className="qb-moneyline">
        <Figure
          label="Overdue tasks"
          size="lg"
          metric="overdue"
          value={<Count value={overdue.length} />}
          tone={overdue.length ? "out" : undefined}
          sub={overdue.length ? "Still open past the due date" : "Nothing overdue"}
        />
        <Figure
          label="Unassigned"
          size="lg"
          metric="risk"
          value={<Count value={unassignedCount} />}
          tone={unassignedCount ? "warn" : undefined}
          sub={unassignedCount ? "Overdue with no owner" : "Every overdue task has an owner"}
        />
        <Figure
          label="Due in 14 days"
          size="lg"
          metric="soon"
          value={<Count value={upcoming.length} />}
          sub="Upcoming due dates"
        />
        <Figure
          label="Late milestones"
          size="lg"
          metric="flag"
          value={<Count value={lateMilestoneCount} />}
          tone={lateMilestoneCount ? "out" : undefined}
          sub={`${data.milestones.length} late or upcoming`}
        />
      </div>
      <div className="qb-two tw-task-pair">
        <Panel
          title="Overdue tasks"
          meta={unassignedCount ? `${overdue.length} · ${unassignedCount} unassigned` : `${overdue.length}`}
        >
          <DataTable data={overdue} columns={taskCols} pageSize={8} empty="Nothing overdue." />
        </Panel>
        <Panel title="Due in the next 14 days" meta={`${upcoming.length}`}>
          <DataTable
            data={upcoming}
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
    </>
  );
}
