"use client";

import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, DueChip, Figure, FilterChips, MiniBar, Panel, Pill } from "../qb-ui";
import {
  describeDue,
  describeProjectDue,
  filterProjects,
  projectUrl,
  taskUrl,
  workByProject,
  type ProjectFilter,
  type ProjectWork,
} from "../../lib/teamwork-derive";
import { Count } from "./kpis";
import type {
  TeamworkMilestone,
  TeamworkOverview,
  TeamworkProject,
  TeamworkTask,
} from "../../types/teamwork";

const HEALTH: Record<string, { label: string; tone: "good" | "bad" | "muted" | "neutral" }> = {
  good: { label: "Good", tone: "good" },
  ok: { label: "OK", tone: "neutral" },
  bad: { label: "At risk", tone: "bad" },
  unset: { label: "Not set", tone: "muted" },
};

function TaskList({
  title,
  items,
  baseUrl,
  todayISO,
  empty,
}: {
  title: string;
  items: TeamworkTask[];
  baseUrl?: string | null;
  todayISO: string;
  empty: string;
}) {
  return (
    <div>
      <h4>{title}</h4>
      {items.length ? (
        <ul>
          {items.map((task) => {
            const href = taskUrl(baseUrl, task.id);
            const due = describeDue(task.due_date, todayISO);
            return (
              <li key={task.id}>
                {href ? (
                  <a href={href} target="_blank" rel="noreferrer">
                    {task.name}
                  </a>
                ) : (
                  <span>{task.name}</span>
                )}
                <DueChip label={due.label} tone={due.tone} />
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="qb-drill-empty">{empty}</p>
      )}
    </div>
  );
}

function MilestoneList({
  items,
  todayISO,
}: {
  items: TeamworkMilestone[];
  todayISO: string;
}) {
  return (
    <div>
      <h4>Milestones</h4>
      {items.length ? (
        <ul>
          {items.map((milestone) => {
            const due = describeDue(milestone.due_date, todayISO);
            return (
              <li key={milestone.id}>
                <span>{milestone.name}</span>
                <DueChip label={due.label} tone={due.tone} />
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="qb-drill-empty">No late or upcoming milestones.</p>
      )}
    </div>
  );
}

function ProjectDrill({
  project,
  work,
  baseUrl,
  todayISO,
}: {
  project: TeamworkProject;
  work?: ProjectWork;
  baseUrl?: string | null;
  todayISO: string;
}) {
  const href = projectUrl(baseUrl, project.id);
  const isEmpty =
    !work || (!work.overdue.length && !work.upcoming.length && !work.milestones.length);

  return (
    <div className="qb-drill">
      {isEmpty ? (
        // The sync only mirrors the overdue and within-14-days task buckets, so an
        // empty drill-down means "no urgent work", never "no tasks". Say so, or the
        // healthiest projects read as empty ones.
        <p className="qb-drill-empty">
          Nothing overdue or due in the next 14 days. Tasks outside those two windows are not
          mirrored, so this is not the project&rsquo;s full task list.
        </p>
      ) : (
        <div className="qb-drill-grid">
          <TaskList
            title="Overdue"
            items={work.overdue}
            baseUrl={baseUrl}
            todayISO={todayISO}
            empty="Nothing overdue."
          />
          <TaskList
            title="Due in 14 days"
            items={work.upcoming}
            baseUrl={baseUrl}
            todayISO={todayISO}
            empty="Nothing due soon."
          />
          <MilestoneList items={work.milestones} todayISO={todayISO} />
        </div>
      )}
      {href ? (
        <a className="qb-drill-link" href={href} target="_blank" rel="noreferrer">
          Open in Teamwork
          <ExternalLink size={12} aria-hidden />
        </a>
      ) : null}
    </div>
  );
}

export function TeamworkProjects({
  data,
  todayISO,
}: {
  data: TeamworkOverview;
  todayISO: string;
}) {
  const [filter, setFilter] = useState<ProjectFilter>("all");
  const work = useMemo(() => workByProject(data), [data]);
  const rows = useMemo(
    () => filterProjects(data.projects, filter, todayISO),
    [data.projects, filter, todayISO],
  );
  const counts = useMemo(
    () => ({
      all: data.projects.length,
      risk: filterProjects(data.projects, "risk", todayISO).length,
      overdue: filterProjects(data.projects, "overdue", todayISO).length,
      soon: filterProjects(data.projects, "soon", todayISO).length,
    }),
    [data.projects, todayISO],
  );

  const columns = useMemo<ColumnDef<TeamworkProject, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Project",
        cell: (c) => {
          const project = c.row.original;
          const href = projectUrl(data.base_url, project.id);
          if (!href) return <span className="qb-name">{project.name}</span>;
          return (
            // No stopPropagation needed: DataTable's isInteractiveTarget guard
            // already leaves clicks and Enter on links to the link itself.
            <a className="qb-name" href={href} target="_blank" rel="noreferrer">
              {project.name}
            </a>
          );
        },
      },
      { accessorKey: "company_name", header: "Client" },
      {
        accessorKey: "health",
        header: "Health",
        cell: (c) => {
          const health = HEALTH[c.getValue<string>()] ?? HEALTH.unset;
          return <Pill label={health.label} tone={health.tone} />;
        },
      },
      {
        accessorKey: "progress_pct",
        header: "Done",
        cell: (c) => {
          const pct = c.getValue<number>() ?? 0;
          return <MiniBar value={pct} max={100} label={`${pct}%`} />;
        },
      },
      {
        accessorKey: "tasks_overdue",
        header: "Overdue",
        meta: { numeric: true },
        cell: (c) => {
          const count = c.getValue<number>() ?? 0;
          return count ? <span className="qb-tone-bad">{count}</span> : <span>0</span>;
        },
      },
      {
        accessorKey: "due_date",
        header: "Due",
        cell: (c) => {
          const due = describeProjectDue(c.row.original, todayISO);
          return <DueChip label={due.label} tone={due.tone} />;
        },
      },
    ],
    [data.base_url, todayISO],
  );

  return (
    <>
      <div className="qb-moneyline">
        <Figure
          label="Active projects"
          size="lg"
          metric="projects"
          value={<Count value={counts.all} />}
          sub={counts.risk ? `${counts.risk} at risk` : "None flagged at risk"}
        />
        <Figure
          label="At risk"
          size="lg"
          metric="risk"
          value={<Count value={counts.risk} />}
          tone={counts.risk ? "warn" : undefined}
          sub={counts.risk ? "Teamwork health is at risk" : "None flagged at risk"}
        />
        <Figure
          label="Has overdue"
          size="lg"
          metric="overdue"
          value={<Count value={counts.overdue} />}
          tone={counts.overdue ? "out" : undefined}
          sub={counts.overdue ? "Projects carrying late tasks" : "No overdue work"}
        />
        <Figure
          label="Due soon"
          size="lg"
          metric="soon"
          value={<Count value={counts.soon} />}
          sub="Project due date within 14 days"
        />
      </div>
      <Panel title="Projects" meta={`${rows.length} of ${data.projects.length}`}>
        <FilterChips
          label="Filter projects"
          value={filter}
          onChange={setFilter}
          options={[
            { id: "all", label: "All", count: counts.all },
            { id: "risk", label: "At risk", count: counts.risk },
            { id: "overdue", label: "Has overdue", count: counts.overdue },
            { id: "soon", label: "Due soon", count: counts.soon },
          ]}
        />
        <DataTable
          data={rows}
          columns={columns}
          initialSort="tasks_overdue"
          pageSize={12}
          rowId={(project) => project.id}
          empty={
            filter === "all"
              ? "No active Teamwork projects for this API user."
              : "No projects match this filter."
          }
          renderSubRow={(project) => (
            <ProjectDrill
              project={project}
              work={work.get(project.id)}
              baseUrl={data.base_url}
              todayISO={todayISO}
            />
          )}
        />
      </Panel>
    </>
  );
}
