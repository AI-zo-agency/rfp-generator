"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, DueChip, Figure, FilterChips, MiniBar, Panel, Pill } from "../qb-ui";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  describeDue,
  describeProjectDue,
  filterProjects,
  formatProjectDate,
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

type WorkSection = "overdue" | "upcoming" | "milestones";

function TaskCard({
  task,
  baseUrl,
  todayISO,
}: {
  task: TeamworkTask;
  baseUrl?: string | null;
  todayISO: string;
}) {
  const href = taskUrl(baseUrl, task.id);
  const due = describeDue(task.due_date, todayISO);
  const assignees = task.assignees.length ? task.assignees.join(", ") : "Unassigned";

  return (
    <article className="tw-task-card">
      {href ? (
        <a className="tw-task-card__name" href={href} target="_blank" rel="noreferrer">
          {task.name}
        </a>
      ) : (
        <span className="tw-task-card__name">{task.name}</span>
      )}
      <div className="tw-task-card__meta">
        <span className={`tw-task-card__assignee${task.assignees.length ? "" : " tw-task-card__assignee--empty"}`}>
          {assignees}
        </span>
        <DueChip label={due.label} tone={due.tone} />
      </div>
    </article>
  );
}

function MilestoneCard({
  milestone,
  todayISO,
}: {
  milestone: TeamworkMilestone;
  todayISO: string;
}) {
  const due = describeDue(milestone.due_date, todayISO);
  const status = (milestone.status || "").toLowerCase();
  const isLate = status === "late";

  return (
    <article className="tw-task-card">
      <span className="tw-task-card__name">{milestone.name}</span>
      <div className="tw-task-card__meta">
        {status ? <Pill label={status} tone={isLate ? "bad" : "neutral"} /> : <span />}
        <DueChip label={due.label} tone={due.tone} />
      </div>
    </article>
  );
}

function WorkSectionPanel({
  title,
  count,
  tone,
  items,
  empty,
  children,
}: {
  title: string;
  count: number;
  tone?: "bad" | "warn" | "neutral";
  items: number;
  empty: string;
  children: ReactNode;
}) {
  return (
    <section className="tw-work-section" data-tone={tone}>
      <header className="tw-work-section__head">
        <h4>{title}</h4>
        <span className="tw-work-section__count">{count}</span>
      </header>
      <div className="tw-work-section__body">
        {items ? children : <p className="tw-work-section__empty">{empty}</p>}
      </div>
    </section>
  );
}

function ProjectPanel({
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
  const overdue = work?.overdue ?? [];
  const upcoming = work?.upcoming ?? [];
  const milestones = work?.milestones ?? [];
  const totalItems = overdue.length + upcoming.length + milestones.length;

  const defaultSection: WorkSection = overdue.length
    ? "overdue"
    : upcoming.length
      ? "upcoming"
      : "milestones";
  const [section, setSection] = useState<WorkSection>(defaultSection);

  const tabs: { id: WorkSection; label: string; count: number }[] = [
    { id: "overdue", label: "Overdue", count: overdue.length },
    { id: "upcoming", label: "Due in 14 days", count: upcoming.length },
    { id: "milestones", label: "Milestones", count: milestones.length },
  ];

  const dateRange =
    project.start_date || project.due_date
      ? [formatProjectDate(project.start_date), formatProjectDate(project.due_date)].join(" → ")
      : null;

  return (
    <div className="tw-project-panel">
      <header className="tw-project-panel__header">
        <div className="tw-project-panel__titleblock">
          <h3 className="tw-project-panel__title">{project.name}</h3>
          <p className="tw-project-panel__meta">
            {project.company_name || "No client"}
            {dateRange ? ` · ${dateRange}` : null}
          </p>
        </div>
      </header>

      {!totalItems ? (
        <p className="tw-project-panel__empty">
          Nothing overdue or due in the next 14 days. Tasks outside those two windows are not
          mirrored, so this is not the project&rsquo;s full task list.
        </p>
      ) : (
        <>
          <div className="tw-project-panel__tabs" role="tablist" aria-label="Project work">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={section === tab.id}
                data-state={section === tab.id ? "on" : undefined}
                onClick={() => setSection(tab.id)}
              >
                {tab.label}
                <span className="tw-project-panel__tabcount">{tab.count}</span>
              </button>
            ))}
          </div>

          <div className="tw-project-panel__content">
            {section === "overdue" ? (
              <div className="tw-task-list" role="tabpanel">
                {overdue.map((task) => (
                  <TaskCard key={task.id} task={task} baseUrl={baseUrl} todayISO={todayISO} />
                ))}
              </div>
            ) : null}
            {section === "upcoming" ? (
              <div className="tw-task-list" role="tabpanel">
                {upcoming.map((task) => (
                  <TaskCard key={task.id} task={task} baseUrl={baseUrl} todayISO={todayISO} />
                ))}
              </div>
            ) : null}
            {section === "milestones" ? (
              <div className="tw-task-list" role="tabpanel">
                {milestones.map((milestone) => (
                  <MilestoneCard key={milestone.id} milestone={milestone} todayISO={todayISO} />
                ))}
              </div>
            ) : null}
          </div>

          <div className="tw-project-panel__desktop">
            <WorkSectionPanel
              title="Overdue"
              count={overdue.length}
              tone="bad"
              items={overdue.length}
              empty="Nothing overdue."
            >
              {overdue.map((task) => (
                <TaskCard key={task.id} task={task} baseUrl={baseUrl} todayISO={todayISO} />
              ))}
            </WorkSectionPanel>
            <WorkSectionPanel
              title="Due in 14 days"
              count={upcoming.length}
              tone="warn"
              items={upcoming.length}
              empty="Nothing due soon."
            >
              {upcoming.map((task) => (
                <TaskCard key={task.id} task={task} baseUrl={baseUrl} todayISO={todayISO} />
              ))}
            </WorkSectionPanel>
            <WorkSectionPanel
              title="Milestones"
              count={milestones.length}
              items={milestones.length}
              empty="No late or upcoming milestones."
            >
              {milestones.map((milestone) => (
                <MilestoneCard key={milestone.id} milestone={milestone} todayISO={todayISO} />
              ))}
            </WorkSectionPanel>
          </div>
        </>
      )}
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
        id: "expand",
        header: "",
        enableSorting: false,
        cell: () => (
          <span className="tw-row-chevron" aria-hidden>
            <ChevronRight size={14} className="tw-row-chevron__icon" />
          </span>
        ),
        meta: { numeric: true },
      },
      {
        accessorKey: "name",
        header: "Project",
        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
      },
      { accessorKey: "company_name", header: "Client" },
      {
        accessorKey: "start_date",
        header: "Start",
        cell: (c) => <span className="tw-date">{formatProjectDate(c.getValue<string | null>())}</span>,
      },
      {
        accessorKey: "due_date",
        header: "End",
        cell: (c) => {
          const project = c.row.original;
          const due = describeProjectDue(project, todayISO);
          return (
            <span className="tw-end-cell">
              <span className="tw-date">{formatProjectDate(project.due_date)}</span>
              {due.tone !== "none" || due.label === "Complete" ? (
                <DueChip label={due.label} tone={due.tone} />
              ) : null}
            </span>
          );
        },
      },
      {
        accessorKey: "health",
        header: "Health",
        cell: (c) => {
          const health = HEALTH[c.getValue<string>()] ?? HEALTH.unset;
          if (health.tone === "muted") {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span><Pill label={health.label} tone={health.tone} /></span>
                </TooltipTrigger>
                <TooltipContent>Set project health in Teamwork to see it here</TooltipContent>
              </Tooltip>
            );
          }
          return <Pill label={health.label} tone={health.tone} />;
        },
      },
      {
        accessorKey: "progress_pct",
        header: "Done",
        cell: (c) => {
          const pct = c.getValue<number>() ?? 0;
          if (!pct) {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span><MiniBar value={0} max={100} label="0%" /></span>
                </TooltipTrigger>
                <TooltipContent>Mark tasks complete in Teamwork to track progress</TooltipContent>
              </Tooltip>
            );
          }
          return <MiniBar value={pct} max={100} label={`${pct}%`} />;
        },
      },
      {
        id: "budget",
        header: "Budget",
        accessorFn: (row) => row.budget_capacity,
        cell: (c) => {
          const p = c.row.original;
          if (!p.budget_capacity) return <span className="qb-muted">—</span>;
          const cap = p.budget_capacity / 100;
          const used = p.budget_used / 100;
          const pct = Math.round((used / cap) * 100);
          return (
            <span className="tw-budget-cell">
              <MiniBar value={pct} max={100} label={`${pct}%`} tone={pct > 90 ? "bad" : pct > 75 ? "warn" : undefined} />
              <span className="tw-budget-sub">${used.toLocaleString()} of ${cap.toLocaleString()}</span>
            </span>
          );
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
        id: "teamwork",
        header: "",
        enableSorting: false,
        cell: (c) => {
          const href = projectUrl(data.base_url, c.row.original.id);
          if (!href) return null;
          return (
            <a
              className="tw-row-link"
              href={href}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${c.row.original.name} in Teamwork`}
              title="Open in Teamwork"
            >
              <ExternalLink size={14} aria-hidden />
            </a>
          );
        },
        meta: { numeric: true },
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
            <ProjectPanel
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
