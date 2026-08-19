"use client";

import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Figure, FilterChips, MiniBar, Panel } from "../qb-ui";
import { billablePct, buildWorkload, hoursLabel, type WorkloadRow } from "../../lib/teamwork-derive";
import type { TeamworkOverview, TeamworkTimeBucket } from "../../types/teamwork";
import { Count, HoursValue } from "./kpis";

type TimeView = "person" | "project";

export function TeamworkTime({ data }: { data: TeamworkOverview }) {
  const [view, setView] = useState<TimeView>("person");

  const buckets = view === "person" ? data.time.by_person : data.time.by_project;
  const maxMinutes = useMemo(
    () => buckets.reduce((max, bucket) => Math.max(max, bucket.minutes), 0),
    [buckets],
  );

  const timeCols = useMemo<ColumnDef<TeamworkTimeBucket, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: view === "person" ? "Person" : "Project",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "minutes",
        header: "Hours",
        meta: { numeric: true },
        cell: (c) => {
          const minutes = c.getValue<number>() ?? 0;
          return <MiniBar value={minutes} max={maxMinutes} label={hoursLabel(minutes)} />;
        },
      },
      {
        id: "billable",
        header: "Billable",
        meta: { numeric: true },
        accessorFn: (row) => billablePct(row.billable_minutes ?? 0, row.minutes),
        cell: (c) => `${c.getValue<number>()}%`,
      },
    ],
    [maxMinutes, view],
  );

  const workload = useMemo(() => buildWorkload(data), [data]);
  const maxWorkMinutes = useMemo(
    () => workload.reduce((max, row) => Math.max(max, row.minutes), 0),
    [workload],
  );

  const workloadCols = useMemo<ColumnDef<WorkloadRow, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Person",
        cell: (c) => {
          const row = c.row.original;
          return (
            <span className="qb-name" title={row.email || undefined}>
              {row.name}
              {row.title ? <span className="qb-tag">{row.title}</span> : null}
            </span>
          );
        },
      },
      {
        accessorKey: "overdue",
        header: "Overdue",
        meta: { numeric: true },
        cell: (c) => {
          const count = c.getValue<number>() ?? 0;
          return count ? <span className="qb-tone-bad">{count}</span> : <span>0</span>;
        },
      },
      { accessorKey: "upcoming", header: "Due soon", meta: { numeric: true } },
      {
        accessorKey: "minutes",
        header: "Hours",
        meta: { numeric: true },
        cell: (c) => {
          const minutes = c.getValue<number>() ?? 0;
          return <MiniBar value={minutes} max={maxWorkMinutes} label={hoursLabel(minutes)} />;
        },
      },
      {
        id: "billable",
        header: "Billable",
        meta: { numeric: true },
        accessorFn: (row) => billablePct(row.billableMinutes, row.minutes),
        cell: (c) => `${c.getValue<number>()}%`,
      },
    ],
    [maxWorkMinutes],
  );

  const overallBillable = billablePct(data.time.billable_minutes, data.time.total_minutes);
  const peopleLogging = data.time.by_person.filter((bucket) => bucket.minutes > 0).length;
  const unbillableMinutes = Math.max(0, data.time.total_minutes - data.time.billable_minutes);
  const through = data.time.period_end
    ? new Date(`${data.time.period_end}T00:00:00Z`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      })
    : null;

  return (
    <>
      <div className="qb-moneyline">
        <Figure
          label="Hours this month"
          size="lg"
          metric="hours"
          value={<HoursValue minutes={data.time.total_minutes} />}
          sub={through ? `Through ${through}` : "Logged this month"}
        />
        <Figure
          label="Billable"
          size="lg"
          metric="margin"
          value={
            <>
              {overallBillable}
              <span className="qb-unit">%</span>
            </>
          }
          sub={`${hoursLabel(data.time.billable_minutes)} of ${hoursLabel(data.time.total_minutes)}`}
        />
        <Figure
          label="People logging"
          size="lg"
          metric="people"
          value={<Count value={peopleLogging} />}
          sub={`${data.people.length} in the directory`}
        />
        <Figure
          label="Non-billable"
          size="lg"
          metric="overdue"
          value={<HoursValue minutes={unbillableMinutes} />}
          sub="Hours not marked billable"
        />
      </div>
      <Panel
        title="Time this month"
        meta={`${hoursLabel(data.time.total_minutes)} · ${overallBillable}% billable`}
      >
        <FilterChips
          label="Group time by"
          value={view}
          onChange={setView}
          options={[
            { id: "person" as TimeView, label: "By person" },
            { id: "project" as TimeView, label: "By project" },
          ]}
        />
        <DataTable
          data={buckets}
          columns={timeCols}
          initialSort="minutes"
          pageSize={10}
          empty="No time logged this month."
        />
      </Panel>

      <Panel title="Workload" meta={`${workload.length} people`}>
        <DataTable
          data={workload}
          columns={workloadCols}
          pageSize={12}
          empty="No assignees or logged time this month."
        />
      </Panel>
    </>
  );
}
