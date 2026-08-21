/**
 * Pure derivation for the Teamwork dashboard.
 *
 * Everything the UI computes lives here so it can be tested without a DOM.
 * Components in components/teamwork/ stay presentational.
 */

import type {
  TeamworkMilestone,
  TeamworkOverview,
  TeamworkProject,
  TeamworkTask,
  TeamworkTimeBucket,
} from "../types/teamwork";

const MS_PER_DAY = 86_400_000;

/**
 * Parse a YYYY-MM-DD (or full ISO) string to UTC midnight.
 *
 * The API sends calendar dates with no timezone. `new Date("2026-08-18")` is
 * UTC midnight, which renders as the 17th anywhere west of Greenwich — so the
 * date component is pulled out by hand and compared in UTC throughout.
 */
export function toUTCDay(value?: string | null): number | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** Whole days from today to the due date. Negative means overdue. */
export function daysUntil(dueDate: string | null | undefined, todayISO: string): number | null {
  const due = toUTCDay(dueDate);
  const today = toUTCDay(todayISO);
  if (due === null || today === null) return null;
  return Math.round((due - today) / MS_PER_DAY);
}

export type DueTone = "late" | "soon" | "later" | "none";

export interface DueDescriptor {
  label: string;
  days: number | null;
  tone: DueTone;
}

/** A due date as a reader sees it: how far away, and whether to worry. */
export function describeDue(
  dueDate: string | null | undefined,
  todayISO: string,
): DueDescriptor {
  const days = daysUntil(dueDate, todayISO);
  if (days === null) return { label: "—", days: null, tone: "none" };
  if (days < 0) return { label: `${Math.abs(days)}d late`, days, tone: "late" };
  if (days === 0) return { label: "Today", days, tone: "soon" };
  if (days <= 3) return { label: `in ${days}d`, days, tone: "soon" };
  return { label: `in ${days}d`, days, tone: "later" };
}

/** Teamwork `status` stays `active`; completion lives on `subStatus`. */
export function projectIsComplete(project: TeamworkProject): boolean {
  return (project.status || "").toLowerCase() === "completed";
}

/** Project due chip: completed work is done, not late. */
export function describeProjectDue(project: TeamworkProject, todayISO: string): DueDescriptor {
  if (projectIsComplete(project)) {
    return { label: "Complete", days: daysUntil(project.due_date, todayISO), tone: "none" };
  }
  return describeDue(project.due_date, todayISO);
}

/** Calendar date for project tables — UTC-safe, Teamwork-style weekday label. */
export function formatProjectDate(value?: string | null): string {
  const day = toUTCDay(value);
  if (day === null) return "—";
  return new Date(day).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function hoursLabel(minutes: number): string {
  const hours = (minutes || 0) / 60;
  if (!hours) return "0h";
  return `${hours.toFixed(hours >= 10 ? 0 : 1)}h`;
}

/** Chart axis / tooltip hours. Same rounding as hoursLabel, as a number. */
export function hoursNumber(minutes: number): number {
  const hours = (minutes || 0) / 60;
  if (!hours) return 0;
  return hours >= 10 ? Math.round(hours) : Math.round(hours * 10) / 10;
}

/** "Sonja A." — first name plus last initial, so an 8-bar axis still fits. */
export function shortPersonName(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) {
    return parts[0].length > 14 ? `${parts[0].slice(0, 13)}…` : parts[0];
  }
  const last = parts.at(-1);
  return last ? `${parts[0]} ${last[0]}.` : parts[0];
}

export interface HoursChartRow {
  name: string;
  hours: number;
  billable: number;
  nonBillable: number;
}

/**
 * Top people by hours this month, ready to plot.
 *
 * `split` is true only when at least one bucket carries a billable slice.
 * Older cache rows omit that field, and a two-series chart would paint
 * every hour as non-billable.
 */
export function hoursChartRows(
  buckets: TeamworkTimeBucket[],
  limit = 8,
): { rows: HoursChartRow[]; split: boolean } {
  const split = buckets.some((bucket) => (bucket.billable_minutes ?? 0) > 0);
  const rows = [...buckets]
    .filter((bucket) => bucket.minutes > 0)
    .sort((a, b) => b.minutes - a.minutes)
    .slice(0, limit)
    .map((bucket) => {
      const billableMinutes = bucket.billable_minutes ?? 0;
      return {
        name: shortPersonName(bucket.name),
        hours: hoursNumber(bucket.minutes),
        billable: hoursNumber(billableMinutes),
        nonBillable: hoursNumber(Math.max(0, bucket.minutes - billableMinutes)),
      };
    });
  return { rows, split };
}

export function billablePct(billableMinutes: number, totalMinutes: number): number {
  if (!totalMinutes) return 0;
  return Math.round((billableMinutes / totalMinutes) * 100);
}

function joinBase(baseUrl: string | null | undefined, path: string): string | null {
  if (!baseUrl) return null;
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

export function projectUrl(baseUrl: string | null | undefined, projectId: string): string | null {
  if (!projectId) return null;
  return joinBase(baseUrl, `/app/projects/${projectId}/tasks`);
}

export function taskUrl(baseUrl: string | null | undefined, taskId: string): string | null {
  if (!taskId) return null;
  return joinBase(baseUrl, `/app/tasks/${taskId}`);
}

export type SectionId = "projects" | "work" | "time";
export type SignalSeverity = "critical" | "warn" | "info";

export interface TeamworkSignal {
  id: string;
  severity: SignalSeverity;
  headline: string;
  detail?: string;
  figure?: string;
  goTo?: SectionId;
}

const SEVERITY_ORDER: Record<SignalSeverity, number> = { critical: 0, warn: 1, info: 2 };

function plural(count: number, word: string): string {
  return count === 1 ? word : `${word}s`;
}

/** "A, B, C +2 more" — enough detail to recognize the problem, not a full list. */
export function nameList(names: string[], max = 3): string {
  const shown = names.slice(0, max);
  const rest = names.length - shown.length;
  return rest > 0 ? `${shown.join(", ")} +${rest} more` : shown.join(", ");
}

/**
 * What is wrong right now, worst first.
 *
 * An empty array means nothing is wrong — the caller renders the all-clear
 * line rather than an empty panel.
 */
export function buildSignals(data: TeamworkOverview, todayISO: string): TeamworkSignal[] {
  const signals: TeamworkSignal[] = [];

  const atRisk = data.projects.filter((p) => p.health === "bad");
  if (atRisk.length) {
    signals.push({
      id: "projects-at-risk",
      severity: "critical",
      headline: `${atRisk.length} ${plural(atRisk.length, "project")} flagged at risk`,
      detail: nameList(atRisk.map((p) => p.name)),
      figure: String(atRisk.length),
      goTo: "projects",
    });
  }

  // An overdue task nobody owns cannot move on its own. Highest-value signal
  // on the tab, and invisible before this change — the old table rendered an
  // empty assignee list as an em dash, identical to missing data.
  const unassigned = data.overdue_tasks.filter((t) => t.assignees.length === 0);
  if (unassigned.length) {
    signals.push({
      id: "overdue-unassigned",
      severity: "critical",
      headline: `${unassigned.length} overdue ${plural(unassigned.length, "task")} with no assignee`,
      detail: nameList(unassigned.map((t) => t.name)),
      figure: String(unassigned.length),
      goTo: "work",
    });
  }

  let oldest: { task: TeamworkTask; days: number } | null = null;
  for (const t of data.overdue_tasks) {
    const days = daysUntil(t.due_date, todayISO);
    if (days === null || days >= 0) continue;
    const late = Math.abs(days);
    if (!oldest || late > oldest.days) oldest = { task: t, days: late };
  }
  if (oldest) {
    signals.push({
      id: "oldest-overdue",
      severity: oldest.days >= 14 ? "critical" : "warn",
      headline: `Oldest overdue task is ${oldest.days} days late`,
      detail: `${oldest.task.name} · ${oldest.task.project_name || "No project"}`,
      figure: `${oldest.days}d`,
      goTo: "work",
    });
  }

  const lateMilestones = data.milestones.filter(
    (m) => (m.status || "").toLowerCase() === "late",
  );
  if (lateMilestones.length) {
    signals.push({
      id: "late-milestones",
      severity: "warn",
      headline: `${lateMilestones.length} late ${plural(lateMilestones.length, "milestone")}`,
      detail: nameList(lateMilestones.map((m) => m.name)),
      figure: String(lateMilestones.length),
      goTo: "work",
    });
  }

  const pastDue = data.projects.filter((p) => {
    if (projectIsComplete(p) || p.progress_pct >= 100) return false;
    const days = daysUntil(p.due_date, todayISO);
    return days !== null && days < 0;
  });
  if (pastDue.length) {
    signals.push({
      id: "projects-past-due",
      severity: "warn",
      headline: `${pastDue.length} ${plural(pastDue.length, "project")} past the due date and not complete`,
      detail: nameList(pastDue.map((p) => p.name)),
      figure: String(pastDue.length),
      goTo: "projects",
    });
  }

  return signals.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

export interface ProjectWork {
  overdue: TeamworkTask[];
  upcoming: TeamworkTask[];
  milestones: TeamworkMilestone[];
}

/**
 * Tasks and milestones keyed by project id, for the projects drill-down.
 *
 * A project with no entry has nothing in the overdue or within-14-days
 * buckets. That is not the same as having no tasks — the sync only mirrors
 * those two slices — so the caller's empty state must say "nothing overdue
 * or due soon", never "no tasks".
 */
export function workByProject(data: TeamworkOverview): Map<string, ProjectWork> {
  const grouped = new Map<string, ProjectWork>();
  const bucket = (projectId: string): ProjectWork => {
    let entry = grouped.get(projectId);
    if (!entry) {
      entry = { overdue: [], upcoming: [], milestones: [] };
      grouped.set(projectId, entry);
    }
    return entry;
  };

  for (const t of data.overdue_tasks) if (t.project_id) bucket(t.project_id).overdue.push(t);
  for (const t of data.upcoming_tasks) if (t.project_id) bucket(t.project_id).upcoming.push(t);
  for (const m of data.milestones) if (m.project_id) bucket(m.project_id).milestones.push(m);

  return grouped;
}

export type ProjectFilter = "all" | "risk" | "overdue" | "soon";

export function filterProjects(
  projects: TeamworkProject[],
  filter: ProjectFilter,
  todayISO: string,
): TeamworkProject[] {
  if (filter === "risk") return projects.filter((p) => p.health === "bad");
  if (filter === "overdue") return projects.filter((p) => p.tasks_overdue > 0);
  if (filter === "soon") {
    return projects.filter((p) => {
      if (projectIsComplete(p)) return false;
      const days = daysUntil(p.due_date, todayISO);
      return days !== null && days >= 0 && days <= 14;
    });
  }
  return projects;
}

export interface WorkloadRow {
  id: string;
  name: string;
  title: string | null;
  email: string;
  overdue: number;
  upcoming: number;
  minutes: number;
  billableMinutes: number;
}

/**
 * Who is carrying what.
 *
 * Joined on lowercased name, because tasks carry assignee names as strings
 * with no ids. Two different people with the same display name would collapse
 * into one row; acceptable for an agency-sized directory.
 */
export function buildWorkload(data: TeamworkOverview): WorkloadRow[] {
  const rows = new Map<string, WorkloadRow>();
  const ensure = (name: string): WorkloadRow | null => {
    const key = name.trim().toLowerCase();
    if (!key) return null;
    let row = rows.get(key);
    if (!row) {
      row = {
        id: key,
        name: name.trim(),
        title: null,
        email: "",
        overdue: 0,
        upcoming: 0,
        minutes: 0,
        billableMinutes: 0,
      };
      rows.set(key, row);
    }
    return row;
  };

  for (const person of data.people) {
    const row = ensure(person.name);
    if (!row) continue;
    row.id = person.id || row.id;
    row.name = person.name;
    row.title = person.title ?? null;
    row.email = person.email || "";
  }
  for (const t of data.overdue_tasks) {
    for (const assignee of t.assignees) {
      const row = ensure(assignee);
      if (row) row.overdue += 1;
    }
  }
  for (const t of data.upcoming_tasks) {
    for (const assignee of t.assignees) {
      const row = ensure(assignee);
      if (row) row.upcoming += 1;
    }
  }
  for (const bucket of data.time.by_person) {
    const row = ensure(bucket.name);
    if (!row) continue;
    row.minutes += bucket.minutes;
    row.billableMinutes += bucket.billable_minutes ?? 0;
  }

  return [...rows.values()].sort(
    (a, b) => b.overdue - a.overdue || b.minutes - a.minutes || a.name.localeCompare(b.name),
  );
}
