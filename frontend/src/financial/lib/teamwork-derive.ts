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

/** Leading job code when present ("EKL 26140 …" → "EKL"), else a short name. */
export function projectShortLabel(name: string): string {
  const first = name.trim().split(/\s+/)[0] || name;
  return first.length > 12 ? `${first.slice(0, 11)}…` : first;
}

export function formatUsdFromCents(cents: number): string {
  const dollars = Math.round((cents || 0) / 100);
  return dollars.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export interface OverdueProjectBucket {
  project_id: string;
  project_name: string;
  count: number;
}

/** Overdue tasks grouped by project, hottest first. */
export function overdueByProject(tasks: TeamworkTask[]): OverdueProjectBucket[] {
  const map = new Map<string, OverdueProjectBucket>();
  for (const task of tasks) {
    const projectId = task.project_id || "";
    const projectName = task.project_name || "No project";
    const key = projectId || `name:${projectName}`;
    const bucket = map.get(key) ?? {
      project_id: projectId,
      project_name: projectName,
      count: 0,
    };
    bucket.count += 1;
    map.set(key, bucket);
  }
  return [...map.values()].sort(
    (a, b) => b.count - a.count || a.project_name.localeCompare(b.project_name),
  );
}

const NEAR_BUDGET_RATIO = 0.75;

export interface BudgetPortfolio {
  capacity: number;
  used: number;
  budgetedCount: number;
  unbudgetedCount: number;
  projectCount: number;
  nearBudget: TeamworkProject[];
}

/** Portfolio spend vs Teamwork financial budgets (amounts in cents). */
export function budgetPortfolio(projects: TeamworkProject[]): BudgetPortfolio {
  let capacity = 0;
  let used = 0;
  let budgetedCount = 0;
  let unbudgetedCount = 0;
  const nearBudget: TeamworkProject[] = [];
  for (const project of projects) {
    const cap = project.budget_capacity || 0;
    const spent = project.budget_used || 0;
    if (cap > 0) {
      budgetedCount += 1;
      capacity += cap;
      used += spent;
      if (spent / cap >= NEAR_BUDGET_RATIO) nearBudget.push(project);
    } else {
      unbudgetedCount += 1;
    }
  }
  return {
    capacity,
    used,
    budgetedCount,
    unbudgetedCount,
    projectCount: projects.length,
    nearBudget,
  };
}

/** Overdue KPI subline: hottest project first, oldest-late as secondary. */
export function describeOverdueHeat(
  overdueTasks: TeamworkTask[],
  oldestLateDays: number,
): string {
  if (!overdueTasks.length) return "Nothing overdue";
  const ranked = overdueByProject(overdueTasks);
  const top = ranked[0];
  const heat = top ? `${top.count} on ${projectShortLabel(top.project_name)}` : "";
  const oldest = oldestLateDays > 0 ? `oldest ${oldestLateDays}d late` : "";
  if (heat && oldest) return `${heat} · ${oldest}`;
  if (heat) return heat;
  return oldest || "No due dates recorded";
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

  const overdueRanked = overdueByProject(data.overdue_tasks);
  if (overdueRanked.length) {
    const top = overdueRanked[0];
    const hot = top.count >= 10;
    if (overdueRanked.length === 1 || hot) {
      signals.push({
        id: "overdue-concentration",
        severity: hot ? "critical" : "warn",
        headline: `${top.project_name} holds ${top.count} overdue ${plural(top.count, "task")}`,
        detail:
          overdueRanked.length > 1
            ? nameList(overdueRanked.slice(1).map((row) => `${row.project_name} (${row.count})`))
            : undefined,
        figure: String(top.count),
        goTo: "work",
      });
    } else {
      const lead = Math.min(3, overdueRanked.length);
      signals.push({
        id: "overdue-concentration",
        severity: "warn",
        headline: `${lead} ${plural(lead, "project")} hold most overdue`,
        detail: nameList(
          overdueRanked.slice(0, lead).map((row) => `${row.project_name} (${row.count})`),
        ),
        figure: String(lead),
        goTo: "work",
      });
    }
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

  const budget = budgetPortfolio(data.projects);
  if (budget.nearBudget.length) {
    signals.push({
      id: "near-budget",
      severity: "warn",
      headline: `${budget.nearBudget.length} ${plural(budget.nearBudget.length, "project")} at or over 75% of budget`,
      detail: nameList(budget.nearBudget.map((p) => p.name)),
      figure: String(budget.nearBudget.length),
      goTo: "projects",
    });
  }
  if (budget.unbudgetedCount > 0) {
    signals.push({
      id: "unbudgeted-projects",
      severity: "warn",
      headline: `${budget.unbudgetedCount} ${plural(budget.unbudgetedCount, "project")} have no financial budget`,
      detail: nameList(
        data.projects.filter((p) => !(p.budget_capacity > 0)).map((p) => p.name),
      ),
      figure: String(budget.unbudgetedCount),
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
